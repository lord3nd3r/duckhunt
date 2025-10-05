"""
Enhanced error handling utilities for DuckHunt Bot
Includes retry mechanisms, circuit breakers, and graceful degradation
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Callable, Any, Optional, Union


class RetryConfig:
    """Configuration for retry mechanisms"""
    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0, 
                 max_delay: float = 60.0, exponential: bool = True):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential = exponential


class CircuitBreaker:
    """Circuit breaker pattern for preventing cascading failures"""
    
    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'closed'  # closed, open, half-open
        self.logger = logging.getLogger('DuckHuntBot.CircuitBreaker')
    
    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if self.state == 'open':
                if self.last_failure_time is not None and time.time() - self.last_failure_time > self.timeout:
                    self.state = 'half-open'
                    self.logger.info("Circuit breaker moving to half-open state")
                else:
                    raise Exception("Circuit breaker is open - operation blocked")
            
            try:
                result = await func(*args, **kwargs)
                if self.state == 'half-open':
                    self.state = 'closed'
                    self.failure_count = 0
                    self.logger.info("Circuit breaker closed - service recovered")
                return result
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()
                
                if self.failure_count >= self.failure_threshold:
                    self.state = 'open'
                    self.logger.warning(f"Circuit breaker opened after {self.failure_count} failures")
                
                raise e
        
        return wrapper


def with_retry(config: Optional[RetryConfig] = None, 
               exceptions: tuple = (Exception,)):
    """Decorator for adding retry logic to functions"""
    
    if config is None:
        config = RetryConfig()
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = logging.getLogger(f'DuckHuntBot.Retry.{func.__name__}')
            
            for attempt in range(config.max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == config.max_attempts - 1:
                        logger.error(f"Function {func.__name__} failed after {config.max_attempts} attempts: {e}")
                        raise
                    
                    delay = config.base_delay
                    if config.exponential:
                        delay *= (2 ** attempt)
                    delay = min(delay, config.max_delay)
                    
                    logger.warning(f"Attempt {attempt + 1}/{config.max_attempts} failed for {func.__name__}: {e}. Retrying in {delay}s")
                    await asyncio.sleep(delay)
            
            return None
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = logging.getLogger(f'DuckHuntBot.Retry.{func.__name__}')
            
            for attempt in range(config.max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == config.max_attempts - 1:
                        logger.error(f"Function {func.__name__} failed after {config.max_attempts} attempts: {e}")
                        raise
                    
                    delay = config.base_delay
                    if config.exponential:
                        delay *= (2 ** attempt)
                    delay = min(delay, config.max_delay)
                    
                    logger.warning(f"Attempt {attempt + 1}/{config.max_attempts} failed for {func.__name__}: {e}. Retrying in {delay}s")
                    time.sleep(delay)
            
            return None
        
        # Return appropriate wrapper based on whether function is async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


class ErrorRecovery:
    """Error recovery and graceful degradation utilities"""
    
    @staticmethod
    def safe_execute(func: Callable, fallback: Any = None, 
                    log_errors: bool = True, logger: Optional[logging.Logger] = None) -> Any:
        """Safely execute a function with fallback value on error"""
        if logger is None:
            logger = logging.getLogger('DuckHuntBot.ErrorRecovery')
        
        try:
            return func()
        except Exception as e:
            if log_errors:
                logger.error(f"Error executing {func.__name__}: {e}")
            return fallback
    
    @staticmethod
    async def safe_execute_async(func: Callable, fallback: Any = None,
                               log_errors: bool = True, logger: Optional[logging.Logger] = None) -> Any:
        """Safely execute an async function with fallback value on error"""
        if logger is None:
            logger = logging.getLogger('DuckHuntBot.ErrorRecovery')
        
        try:
            return await func()
        except Exception as e:
            if log_errors:
                logger.error(f"Error executing {func.__name__}: {e}")
            return fallback
    
    @staticmethod
    def validate_input(value: Any, validator: Callable, default: Any = None,
                      field_name: str = "input") -> Any:
        """Validate input with fallback to default"""
        try:
            if validator(value):
                return value
            else:
                raise ValueError(f"Validation failed for {field_name}")
        except Exception:
            return default


class HealthChecker:
    """Health monitoring and alerting"""
    
    def __init__(self, check_interval: float = 30.0):
        self.check_interval = check_interval
        self.checks = {}
        self.logger = logging.getLogger('DuckHuntBot.Health')
    
    def add_check(self, name: str, check_func: Callable, critical: bool = False):
        """Add a health check function"""
        self.checks[name] = {
            'func': check_func,
            'critical': critical,
            'last_success': None,
            'failure_count': 0
        }
    
    async def run_checks(self) -> dict:
        """Run all health checks and return results"""
        results = {}
        
        for name, check in self.checks.items():
            try:
                result = await check['func']() if asyncio.iscoroutinefunction(check['func']) else check['func']()
                check['last_success'] = time.time()
                check['failure_count'] = 0
                results[name] = {'status': 'healthy', 'result': result}
            except Exception as e:
                check['failure_count'] += 1
                results[name] = {
                    'status': 'unhealthy', 
                    'error': str(e),
                    'failure_count': check['failure_count']
                }
                
                if check['critical'] and check['failure_count'] >= 3:
                    self.logger.error(f"Critical health check '{name}' failed {check['failure_count']} times: {e}")
        
        return results


def safe_format_message(template: str, **kwargs) -> str:
    """Safely format message templates with error handling"""
    try:
        return template.format(**kwargs)
    except KeyError as e:
        logger = logging.getLogger('DuckHuntBot.MessageFormat')
        logger.error(f"Missing template variable {e} in message: {template[:100]}...")
        
        # Try to provide safe fallback
        safe_kwargs = {}
        for key, value in kwargs.items():
            try:
                safe_kwargs[key] = str(value) if value is not None else ''
            except Exception:
                safe_kwargs[key] = ''
        
        # Replace missing variables with placeholders
        import re
        def replace_missing(match):
            var_name = match.group(1)
            if var_name not in safe_kwargs:
                return f"[{var_name}]"
            return f"{{{var_name}}}"
        
        safe_template = re.sub(r'\{([^}]+)\}', replace_missing, template)
        
        try:
            return safe_template.format(**safe_kwargs)
        except Exception:
            return f"[Message format error in template: {template[:50]}...]"
    except Exception as e:
        logger = logging.getLogger('DuckHuntBot.MessageFormat')
        logger.error(f"Unexpected error formatting message: {e}")
        return f"[Message error: {template[:50]}...]"


def sanitize_user_input(value: str, max_length: int = 100, 
                       allowed_chars: Optional[str] = None) -> str:
    """Sanitize user input to prevent injection and errors"""
    if not isinstance(value, str):
        value = str(value)
    
    # Limit length
    value = value[:max_length]
    
    # Remove/replace dangerous characters
    value = value.replace('\r', '').replace('\n', ' ')
    
    # Filter to allowed characters if specified
    if allowed_chars:
        value = ''.join(c for c in value if c in allowed_chars)
    
    return value.strip()