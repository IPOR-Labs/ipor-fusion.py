import logging


class LoggingConfig:

    # Get individual logger for a specific component
    def get_logger(component_name: str):
        logger = logging.getLogger(f"IPOR Fusion CLI - {component_name}")
        logger.setLevel(logging.DEBUG)  # Changed to DEBUG for more detailed logging

        if logger.hasHandlers():
            logger.handlers.clear()

        # Create handlers
        console_handler = logging.StreamHandler()

        # Create formatters and add it to handlers
        log_format = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(log_format)

        logger.propagate = False

        # Add handlers to the logger
        logger.addHandler(console_handler)

        return logger
