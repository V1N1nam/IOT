@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Portas seriais encontradas ===
py -m serial.tools.list_ports -v
echo.
py -c "import config; print('Porta configurada no .env (SERIAL_PORT):', config.SERIAL_PORT)"
echo.
echo Se a porta do Arduino for diferente, ajuste SERIAL_PORT no .env e rode de novo.
echo Lembre de FECHAR o Monitor Serial da Arduino IDE.
echo.
pause
start "Backend - Monitor Sala" cmd /k py backend.py
timeout /t 5 /nobreak >nul
start "Gateway - Monitor Sala" cmd /k py gateway.py
timeout /t 3 /nobreak >nul
start "" http://localhost:5000
