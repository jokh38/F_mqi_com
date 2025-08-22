"""
Configuration management module for the MQI Communicator.

This module provides robust configuration loading, validation, and access
with schema validation and default value handling.
"""

import os
import yaml
from typing import Any, Dict, Optional
from pathlib import Path


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


class ConfigManager:
    """
    Manages application configuration with validation and default values.
    
    Provides dot notation access to configuration values and validates
    configuration against a predefined schema.
    """

    # Configuration schema with required fields and their types
    SCHEMA = {
        "logging": {
            "required": False,
            "fields": {
                "path": {"type": str, "default": "communicator_local.log"}
            }
        },
        "database": {
            "required": True,
            "fields": {
                "path": {"type": str, "required": True}
            }
        },
        "dashboard": {
            "required": False,
            "fields": {
                "auto_start": {"type": bool, "default": True}
            }
        },
        "hpc": {
            "required": True,
            "fields": {
                "host": {"type": str, "required": True},
                "user": {"type": str, "required": True},
                "remote_base_dir": {"type": str, "required": True},
                "remote_command": {"type": str, "required": True},
                "scp_command": {"type": str, "default": "scp"},
                "ssh_command": {"type": str, "default": "ssh"},
                "pueue_command": {"type": str, "default": "pueue"},
            }
        },
        "scanner": {
            "required": True,
            "fields": {
                "watch_path": {"type": str, "required": True},
                "quiescence_period_seconds": {"type": int, "default": 5},
            }
        },
        "main_loop": {
            "required": True,
            "fields": {
                "sleep_interval_seconds": {"type": int, "default": 10},
                "running_case_timeout_hours": {"type": int, "default": 24},
            }
        },
        "pueue": {
            "required": True,
            "fields": {
                "groups": {"type": list, "required": True},
            }
        },
    }

    def __init__(self, config_path: str):
        """
        Initialize ConfigManager with configuration file.
        
        Args:
            config_path: Path to the YAML configuration file
            
        Raises:
            ConfigValidationError: If configuration is invalid or missing
        """
        self.config_path = config_path
        self.config = self._load_and_validate_config()

    def _load_and_validate_config(self) -> Dict[str, Any]:
        """Load and validate configuration from file."""
        # Check if config file exists
        if not Path(self.config_path).exists():
            raise ConfigValidationError(f"Config file not found: {self.config_path}")

        # Load YAML
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigValidationError(f"Invalid YAML format in {self.config_path}: {e}")

        if not isinstance(config, dict):
            raise ConfigValidationError("Configuration must be a YAML dictionary")

        # Apply defaults and validate
        validated_config = self._apply_defaults_and_validate(config)
        return validated_config

    def _apply_defaults_and_validate(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply default values and validate configuration against schema."""
        validated_config = {}

        for section_name, section_schema in self.SCHEMA.items():
            # Check required sections
            if section_schema.get("required", False) and section_name not in config:
                raise ConfigValidationError(f"Missing required section: {section_name}")

            # Get or create section
            section_config = config.get(section_name, {})
            validated_section = {}

            # Process fields in this section
            for field_name, field_schema in section_schema["fields"].items():
                field_key = f"{section_name}.{field_name}"
                
                # Check if field exists
                if field_name in section_config:
                    field_value = section_config[field_name]
                    # Validate type
                    expected_type = field_schema["type"]
                    if not isinstance(field_value, expected_type):
                        raise ConfigValidationError(
                            f"Invalid type for {field_key}: expected {expected_type.__name__}, "
                            f"got {type(field_value).__name__}"
                        )
                    validated_section[field_name] = field_value
                elif field_schema.get("required", False):
                    raise ConfigValidationError(f"Missing required field: {field_key}")
                elif "default" in field_schema:
                    validated_section[field_name] = field_schema["default"]

            if validated_section:  # Only add section if it has content
                validated_config[section_name] = validated_section

        return validated_config

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.
        
        Args:
            key: Configuration key in dot notation (e.g., 'hpc.host')
            default: Default value to return if key not found
            
        Returns:
            Configuration value
            
        Raises:
            ConfigValidationError: If key not found and no default provided
        """
        parts = key.split('.')
        current = self.config

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                if default is not None:
                    return default
                raise ConfigValidationError(f"Configuration key not found: {key}")

        return current

    def get_section(self, section_name: str) -> Dict[str, Any]:
        """
        Get entire configuration section.
        
        Args:
            section_name: Name of the configuration section
            
        Returns:
            Dictionary containing the section configuration
            
        Raises:
            ConfigValidationError: If section not found
        """
        if section_name not in self.config:
            raise ConfigValidationError(f"Configuration section not found: {section_name}")
        
        return self.config[section_name].copy()

    def reload(self) -> None:
        """Reload configuration from file."""
        self.config = self._load_and_validate_config()