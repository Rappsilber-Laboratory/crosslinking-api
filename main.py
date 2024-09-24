import os
import uvicorn
from app.api import app
from db_config_parser import API_port


if __name__ == "__main__":
    port = int(API_port())
    print("Port Number: " + str(port))
    uvicorn.run(app,
                host="0.0.0.0",
                port=port,
                log_level="info",  # Set the desired log level ("debug", "info", "warning", etc.)
                log_config="logging.ini",  # Specify the path to your logging configuration file
                timeout_keep_alive=300,  # Keep-alive timeout in seconds
                )
