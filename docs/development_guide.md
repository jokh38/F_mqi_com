# MQI Communicator - Development Guide

## 1. Introduction

To ensure the long-term stability and maintainability of the MQI Communicator project, we adhere to a strict set of development standards, including Test-Driven Development (TDD) and static analysis. This guide provides instructions on how to set up your environment and run the necessary quality checks before committing code.

Following these steps will help prevent common errors and ensure that our codebase remains consistent, readable, and robust.

## 2. Initial Setup

First, ensure you have Python 3.10+ installed. Then, create a virtual environment and install the required dependencies:

```bash
# Create a virtual environment (do this once)
python -m venv .venv

# Activate the virtual environment
# On Windows:
# .\.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# Install all required packages
pip install -r requirements.txt
```

## 3. Running the Test Suite

All new functionality must be accompanied by tests. We use `pytest` to run our tests and `pytest-cov` to measure code coverage, which must be maintained at **85% or higher**.

### How to Run Tests

Due to the project's package structure, you must use the `python -m` flag to ensure `pytest` can correctly locate the `src` module. Run tests from the root directory of the project:

```bash
python -m pytest --cov=src
```

This command will:
- Discover and run all tests in the `tests/` directory.
- Generate a coverage report for the `src/` directory.

## 4. Static Analysis Workflow

Before committing any code, you must run the following three checks.

### Step 1: Format Code with `black`

`black` is our code formatter. It ensures a consistent style across the entire project.

```bash
black src tests
```

### Step 2: Lint with `flake8`

`flake8` checks for logical errors, style violations, and other potential issues. We have configured it to be compatible with `black`.

**Configuration (`.flake8` file):**
- `max-line-length = 88`: This matches `black`'s default line length.
- `extend-ignore = E203, W503`: These are rules that conflict with `black`'s formatting style, so we ignore them.

Run `flake8` from the root directory:

```bash
flake8 src tests
```

If this command produces no output, your code is clean.

### Step 3: Type Check with `mypy`

`mypy` performs static type checking to find type-related errors.

```bash
mypy src
```

**Handling Missing Stubs:**
If you see an error like `Library stubs not installed for "some_library"`, it means `mypy` needs type hints for that library. You can install them with `pip`. For example, we needed stubs for `PyYAML`:

```bash
pip install types-PyYAML
```

After installing new type stubs, remember to add them to the `requirements.txt` file.

## 5. Recommended Pre-Commit Workflow

To avoid issues, please follow this sequence before every commit:

1.  **Format your code:**
    ```bash
    black src tests
    ```
2.  **Lint your code:**
    ```bash
    flake8 src tests
    ```
3.  **Type-check your code:**
    ```bash
    mypy src
    ```
4.  **Run all tests:**
    ```bash
    python -m pytest --cov=src
    ```

Only commit your code after all four of these checks pass successfully.
