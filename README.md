# vasco-r2d2

Suite de automação em Python para o Elite Dangerous, a correr em Linux
Nobara (KDE Plasma 6, Wayland puro, sem X11) via Steam/Proton. Usa visão
computacional (OpenCV, template matching) sobre capturas de ecrã ao vivo
para detetar o estado da UI do jogo, e injeta input de teclado/rato via
`ydotool`. Orquestra um ciclo de comércio completo — comprar um rare good
numa estação, viajar até um fleet carrier via supercruise, vender, e
voltar — coordenado por `vasco.py`.

Projeto pessoal de automação para uso próprio; não é um produto genérico
e assume o setup específico documentado abaixo.

Para detalhe técnico (arquitetura, decisões de design, histórico de bugs
corrigidos), ver [`CLAUDE_CODE_BRIEFING.md`](./CLAUDE_CODE_BRIEFING.md) —
este README é só a porta de entrada.

## Stack

- Nobara Linux, KDE Plasma 6, Wayland (sem X11)
- Captura de ecrã: PipeWire via `xdg-desktop-portal-kde`
- Input: `ydotool` (uinput) com scancodes explícitos
- Jogo: Elite Dangerous via Steam/Proton
- Base de dados: Postgres partilhado (observações de linha de vista
  orbital planeta↔carrier)

## Setup

1. **Dependências Python**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Grupo `input`** (necessário para `evdev_keyboard.py` ler o teclado
   sem root):
   ```bash
   sudo usermod -aG input $USER   # requer logout/login
   ```

3. **`ydotoold`** a correr (input injection):
   ```bash
   systemctl --user status ydotoold
   ```

4. **Base de dados** — copiar `.env.example` para `.env` e preencher:
   ```bash
   cp .env.example .env
   ```
   Variáveis: `R2D2_DB_HOST`, `R2D2_DB_PORT`, `R2D2_DB_NAME`, `R2D2_DB_USER`
   (opcionais, têm defaults em `los_checker.py`). A password não é lida do
   `.env` nem do código -- vem do `~/.pgpass` (formato
   `host:port:database:username:password`), resolvido automaticamente pelo
   libpq. O `.env` nunca deve ser commitado.

## Uso

Ciclo completo, com paragem para confirmar no fim:
```bash
python vasco.py
```

Modo automático desde o arranque (sem perguntar nada entre ciclos):
```bash
python vasco.py a
```

Execução manual de um módulo isolado (útil para testar/calibrar):
```bash
python menu.py
```

Laboratórios de calibração/teste (um por módulo, em `debug/`) espelham as
zonas e templates de produção — usar para recalibrar thresholds ou zonas
`MONITOR_*` sem arriscar a automação real.
