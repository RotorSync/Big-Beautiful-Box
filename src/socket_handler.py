#!/usr/bin/env python3
"""
Socket handler for BLE server communication.

Handles communication with the rotorsync BLE server via localhost socket.
Supports commands from iPad app.
"""

import socket
import threading
import time
import json
from typing import Callable, Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)


class SocketHandler:
    """
    Socket command listener for BLE server communication.
    
    Listens on localhost for commands from the rotorsync BLE server.
    """
    
    # Default port for dashboard commands
    DEFAULT_PORT = 9999
    
    def __init__(
        self,
        port: int = DEFAULT_PORT,
        log_file: Optional[str] = None
    ):
        """
        Initialize socket handler.
        
        Args:
            port: Port to listen on
            log_file: Optional debug log file
        """
        self.port = port
        self.log_file = log_file
        
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._handlers: Dict[str, Callable[[str], Optional[str]]] = {}
        self._default_handler: Optional[Callable[[str], Optional[str]]] = None
    
    def _log(self, message: str) -> None:
        """Write to debug log if configured."""
        if self.log_file:
            try:
                with open(self.log_file, 'a') as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - Socket: {message}\n")
            except Exception:
                pass
    
    def register_handler(
        self,
        command: str,
        callback: Callable[[str], Optional[str]]
    ) -> None:
        """
        Register a handler for a command.
        
        Args:
            command: Command string to handle
            callback: Function that takes the full line and returns optional response
        """
        self._handlers[command] = callback
    
    def set_default_handler(
        self,
        callback: Callable[[str], Optional[str]]
    ) -> None:
        """Set handler for unrecognized commands."""
        self._default_handler = callback
    
    def start(self) -> bool:
        """
        Start the socket listener.
        
        Returns:
            True if started successfully
        """
        if self._running:
            return True
        
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(("127.0.0.1", self.port))
            self._server.listen(1)
            self._server.settimeout(1.0)
            
            self._running = True
            self._thread = threading.Thread(target=self._listen_loop, daemon=True)
            self._thread.start()
            
            logger.info(f"Socket listener started on port {self.port}")
            self._log(f"Started on port {self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Socket start failed: {e}")
            self._log(f"Start failed: {e}")
            return False
    
    def stop(self) -> None:
        """Stop the socket listener."""
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
    
    def _listen_loop(self) -> None:
        """Main listening loop."""
        while self._running:
            try:
                try:
                    client, addr = self._server.accept()
                    client.settimeout(5.0)
                    
                    try:
                        data = client.recv(4096).decode("utf-8").strip()
                        if data:
                            for line in data.split("\n"):
                                line = line.strip()
                                if not line:
                                    continue
                                
                                self._log(f"Received: '{line}'")
                                response = self._handle_command(line)
                                
                                if response:
                                    client.send(response.encode())
                                else:
                                    client.send(b"OK\n")
                                    
                    except socket.timeout:
                        pass
                    finally:
                        client.close()
                        
                except socket.timeout:
                    pass
                    
            except Exception as e:
                if self._running:
                    logger.error(f"Socket error: {e}")
                    self._log(f"Error: {e}")
                    time.sleep(1)
    
    def _handle_command(self, line: str) -> Optional[str]:
        """
        Handle a received command.
        
        Args:
            line: Full command line
            
        Returns:
            Optional response string
        """
        # Check for exact match first
        if line in self._handlers:
            try:
                return self._handlers[line](line)
            except Exception as e:
                logger.error(f"Handler error for '{line}': {e}")
                return None
        
        # Check for prefix matches (e.g., "BATCHMIX:...")
        for prefix, handler in self._handlers.items():
            if line.startswith(prefix + ":"):
                try:
                    return handler(line)
                except Exception as e:
                    logger.error(f"Handler error for '{prefix}': {e}")
                    return None
        
        # Try default handler
        if self._default_handler:
            try:
                return self._default_handler(line)
            except Exception as e:
                logger.error(f"Default handler error: {e}")
        
        return None


def parse_batchmix_data(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse BATCHMIX JSON data from command line.
    
    Args:
        line: Full command line (BATCHMIX:{"...})
        
    Returns:
        Parsed JSON dict or None on error
    """
    try:
        if line.startswith("BATCHMIX:"):
            json_str = line[9:]
            return json.loads(json_str)
    except Exception as e:
        logger.error(f"BatchMix parse error: {e}")
    return None
