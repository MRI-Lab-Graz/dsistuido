#!/usr/bin/env python3
"""
Utility functions for DSI Studio connectivity extraction toolkit
"""

import os
import logging
from datetime import datetime


def create_logs_directory():
    """Create logs directory if it doesn't exist."""
    logs_dir = 'logs'
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def setup_timestamped_logging(script_name='dsistudio', level=logging.INFO):
    """Set up logging with timestamped filename in logs folder."""
    logs_dir = create_logs_directory()
    
    # Generate timestamped log filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(logs_dir, f'{script_name}_{timestamp}.log')
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file)
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info(f"📄 Log file: {log_file}")
    
    return logger, log_file


def get_session_info():
    """Get session information for logging."""
    return {
        'timestamp': datetime.now().isoformat(),
        'datetime_str': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
