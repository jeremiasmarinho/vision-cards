@echo off
echo ============================================================
echo  LAB TEST — Overlay de Transmissao PLO6 (2 jogadores)
echo ============================================================
echo.

echo [PASSO 1] Iniciando Cerebro Central...
start "Cerebro Central" cmd /k "cd cerebro-central && python server.py"
timeout /t 3 /nobreak > nul

echo [PASSO 2] Iniciando Vision-Worker P1 (janela esquerda, offset-x=0)...
start "Vision P1" cmd /k "cd vision-worker && python main.py --player 1"
timeout /t 1 /nobreak > nul

echo [PASSO 3] Iniciando Vision-Worker P2 (janela direita)...
echo.
echo  ATENCAO: Ajuste o --offset-x abaixo conforme a largura da janela do emulador.
echo  Para calibrar: cd vision-worker ^&^& python scan_window_offset.py
echo.
echo  Valor atual: --offset-x 561  (metade da screenshot 1122px)
echo  Se as cartas do P2 nao forem lidas, ajuste este valor.
echo.
start "Vision P2" cmd /k "cd vision-worker && python main.py --player 2 --offset-x 561"

echo.
echo [PASSO 4] Aguarde 5 segundos para os workers conectarem...
timeout /t 5 /nobreak > nul

echo [PASSO 5] Rodando teste de integracao mock (sem captura de tela)...
python mock_lab_test.py

echo.
echo ============================================================
echo  Para verificar o estado consolidado do servidor:
echo    curl http://localhost:3000/health
echo    curl http://localhost:3000/
echo ============================================================
echo.
pause
