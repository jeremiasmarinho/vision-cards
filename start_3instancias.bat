@echo off
echo [INFO] Iniciando 3 instancias (mesas) do HUD...

echo [1/3] Cerebro Central...
start "Cerebro" cmd /k "cd /d %~dp0cerebro-central && python server.py"

echo [2/3] HUD UI...
start "HUD UI" cmd /k "cd /d %~dp0hud-ui && python -m http.server 8080"

echo [3/3] Vision-workers (3 mesas)...
start "Vision 3 instancias" cmd /k "cd /d %~dp0vision-worker && python start_3instancias.py"

echo.
echo [OK] Cerebro, HUD e 3 workers iniciados.
echo     Para calibrar offsets: cd vision-worker ^&^& python calibra_instancias.py
pause
