@echo off
echo [INFO] Iniciando Operacao Colmeia...

echo [1/3] Levantando Cerebro Central (Node.js)...
start "Cerebro Central" cmd /k "cd cerebro-central && npm run dev"

echo [2/3] Levantando Interface do HUD (Python HTTP)...
start "HUD UI" cmd /k "cd hud-ui && python -m http.server 8080"

echo [3/3] Levantando Motor de Visao (Python OpenCV)...
start "Olho do Bot" cmd /k "cd vision-worker && python main.py"

echo [INFO] Todos os sistemas foram acionados.
exit
