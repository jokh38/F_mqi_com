물론입니다. 지금까지 논의된 모든 내용을 종합하여, 개발팀이 참고할 최종 통합 구현 사양서를 작성해 드리겠습니다.

-----

## MQI Communicator: 최종 통합 구현 사양서

### 1\. 프로젝트 개요

#### 1.1. 목표

로컬 Windows PC에서 생성된 신규 QA 케이스를 감지하여 원격 Ubuntu HPC로 전송, 자동 시뮬레이션 후 결과를 다시 로컬 PC로 가져오는 전 과정을 자동화합니다.

#### 1.2. 핵심 설계 원칙

본 시스템은 폐쇄망 환경에서 최소한의 유지보수로 장기간 안정적 운영을 목표로 하며, 다음 원칙을 따릅니다.

  * **단순성 (Simplicity)**: 필수 라이브러리 외 외부 의존성을 최소화하고, 운영체제 기본 도구를 우선 활용합니다.
  * **안정성 (Stability)**: `NSSM`, `systemd` 등 검증된 도구를 사용하여 서비스의 안정적인 구동을 보장합니다.
  * **명확한 역할 분리 (Separation of Concerns)**: 데이터베이스는 **상태(Status)** 관리에, 로그는 **과정(Process)** 기록에 사용하는 등 각 구성 요소의 역할을 명확히 합니다.

-----

### 2\. 시스템 아키텍처

#### 2.1. 구성 요소 및 기술 스택

| 구분 | 위치 | 주요 역할 | 기술 스택 |
| :--- | :--- | :--- | :--- |
| **제어/저장소 PC** | 로컬 Windows | 케이스 감지, 워크플로우 제출, DB 관리, 로컬 로깅 | `Python`, `Watchdog`, `Pueue Client`, `SQLite`, `NSSM` |
| **실행 서버** | 원격 Ubuntu | 작업 실행, 시뮬레이션, 원격 로깅 | `Pueue Daemon`, `Python`, `scp`, `systemd` |

#### 2.2. 데이터 흐름

신규 케이스 감지 → `scp`로 HPC에 업로드 → `Pueue`를 통해 시뮬레이션 순차 실행 → 결과 파일을 `scp`로 로컬에 다운로드 → 모든 과정의 상태는 DB에 기록되고, 상세 로그는 파일/Pueue로 분리 기록됩니다.

-----

### 3\. 최종 파일 구조

```
MQI_Communicator/
├── config/
│   └── config.yaml             # 시스템 전체 설정 파일
├── remote_scripts/             # 원격 HPC에서 실행될 스크립트
│   ├── interpreter.py          # 1차 시뮬레이션 스크립트
│   └── moquisim.py             # 2차 시뮬레이션 스크립트
├── src/                        # 로컬 PC 애플리케이션 소스
│   ├── __init__.py
│   ├── main.py                 # 애플리케이션 시작점, 로깅 설정 포함
│   ├── dashboard.py            # Rich 기반 CLI 대시보드 (선택 사항)
│   ├── common/
│   │   ├── __init__.py
│   │   └── db_manager.py       # SQLite DB 관리 모듈
│   └── services/
│       ├── __init__.py
│       ├── case_scanner.py     # 신규 케이스 감지 모듈
│       └── workflow_submitter.py # Pueue 워크플로우 생성 및 제출 모듈
├── backups/
│   └── backup.bat              # DB 백업 스크립트
└── requirements.txt            # Python 의존성 목록
```

-----

### 4\. 데이터베이스 명세 (SQLite)

데이터베이스는 **상태 추적**을 위해서만 사용하며, 상세 로그를 저장하지 않습니다.

#### 4.1. `cases` 테이블

케이스의 생성부터 완료까지 전반적인 상태를 관리합니다.

| 컬럼명 | 타입 | 설명 |
| :--- | :--- | :--- |
| `case_id` | INTEGER | **Primary Key**, 자동 증가. |
| `case_path` | TEXT | 케이스 원본 디렉토리 경로. |
| `status` | TEXT | 현재 상태 (`submitted`, `running`, `completed`, `failed`). |
| `progress` | INTEGER | 워크플로우 진행률 (0\~100). |
| `pueue_group` | TEXT | 할당된 원격 Pueue 그룹명 (e.g., `gpu0`). |
| `submitted_at`| DATETIME | 작업 제출 시간. |
| `completed_at`| DATETIME | 작업 완료/실패 시간. |

#### 4.2. `gpu_resources` 테이블

원격 자원의 할당 상태를 관리합니다.

| 컬럼명 | 타입 | 설명 |
| :--- | :--- | :--- |
| `pueue_group` | TEXT | **Primary Key.** Pueue 그룹명. |
| `status` | TEXT | 자원 상태 (`available` 또는 `busy`). |
| `assigned_case_id`| INTEGER | 현재 할당된 `cases`의 `case_id`. |

-----

### 5\. 로깅 및 디버깅 전략

#### 5.1. 기본 원칙

  * **DB**: **"무엇(What)"** 이 **"어떤 상태(Status)"** 인지를 기록합니다. (예: `case_001`은 `failed` 상태)
  * **Logs**: **"왜(Why)"** 그 상태가 되었는지 상세한 **"과정(Process)"** 을 기록합니다.

#### 5.2. 개발 환경 로깅 (로컬 파일)

로컬 애플리케이션의 상세한 동작을 추적하기 위해 `RotatingFileHandler`를 사용합니다. `src/main.py`에 아래 함수를 추가하고 실행 초기에 호출합니다.

```python
# src/main.py
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

# Define Korea Standard Time (KST)
KST = timezone(timedelta(hours=9))


class KSTFormatter(logging.Formatter):
    """A logging formatter that uses KST for timestamps."""

    def formatTime(
        self, record: logging.LogRecord, datefmt: Optional[str] = None
    ) -> str:
        dt = datetime.fromtimestamp(record.created, KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


def setup_logging(config: Dict[str, Any]) -> None:
    """Sets up file-based, timezone-aware logging for the application."""
    log_config = config.get("logging", {})
    log_path = log_config.get("path", "communicator_fallback.log")

    log_formatter = KSTFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    log_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5)
    log_handler.setFormatter(log_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(log_handler)

    # Add a console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    logging.info(f"Logger has been configured. Logging to: {log_path}")
```

  * **확인 방법**: VS Code 등 텍스트 편집기에서 `communicator_local.log` 파일을 열어 실시간으로 변경 사항을 확인합니다.

#### 5.3. 원격 환경 로깅 (Pueue)

원격 HPC에서 실행되는 스크립트의 로그는 SSH 접속 후 `pueue` 명령어로 확인합니다.

  * **상태 확인**: `pueue status`로 현재 작업들의 **Task ID**를 확인합니다.
  * **실시간 로그**: `pueue log <Task_ID> -f` 명령어로 특정 작업의 로그를 실시간으로 확인합니다.
  * **전체 로그**: `pueue log <Task_ID>` 명령어로 완료된 작업의 전체 로그를 확인합니다.
  
  
-----

### 6\. Dependencies

# For parsing the YAML configuration file
pyyaml

# For monitoring file system events (e.g., new directory creation)
watchdog

# For creating a rich, text-based user interface (TUI) for the dashboard
rich

# pueue : CLI for scheduling  
  https://github.com/Nukesor/pueue
  
  