# Nota — `git stash` pendente: leitura da distância no HUD

**Stash:** `stash@{0}` — `"wip: pipeline leitura distancia HUD (digitos 0-9/k/m/Ls, docking
validar_e_corrigir_distancia) -- a rever antes de commit"`

Criado em 2026-09-17. Para ver o conteúdo sem aplicar: `git stash show -p stash@{0}`.
Para retomar: `git stash pop` (só depois de resolver os bloqueios abaixo).

## O que está no stash

Alterações a `docking.py`, `supercruise_assist.py` e `requirements.txt` que
implementam leitura da distância ao alvo (`X.XXkm`/`Mm`/`Ls`) diretamente do
HUD por template matching de dígitos, para substituir a manobra "boost e
reza" na aproximação final por uma aproximação controlada:

- `supercruise_assist.ler_distancia_hud()` — lê o texto junto ao retículo
  numa região **fixa** (`MONITOR_DISTANCIA`) e devolve o valor em km.
- `executar_aproximacao_controlada()` — acelera/corta o motor com base
  nessa leitura em vez de um boost às cegas.
- `executar_recuo_emergencia()` / `validar_e_corrigir_distancia()` — usados
  por `docking.py` para reagir a distâncias fora do intervalo seguro
  (recuar se `<4km`, aproximar se `>7.5km`) antes do pedido de docking.

## Porque não foi commitado

Dois problemas confirmados em teste real (screenshots ao vivo, 2026-09-17),
que a versão no stash **não resolve** por assumir uma região fixa:

1. **Posição dinâmica** — a etiqueta "distância/nome" está presa ao alvo no
   espaço 3D, não a uma posição fixa do ecrã; muda com a orientação da nave.
   `MONITOR_DISTANCIA` só "funciona" nos frames onde calhou a nave estar
   virada de um jeito parecido ao da calibração.
2. **Contaminação por fundo colorido** — quando a estrutura da estação/
   asteroide atrás do texto tem tom de cor parecido ao do texto (confirmado
   nos dois regimes: verde "km" e laranja/âmbar "Mm"/"Ls"), a segmentação
   por cor funde ruído com os caracteres e a leitura falha ou fragmenta.

## Caminho já validado para continuar

`debug/coletar_digitos_hud.py` (commit `b444a1b`, já em produção do
repositório mas **não usado por nenhum script de voo**) resolve o problema 1
(busca numa janela central em vez de posição fixa, cobre os dois regimes de
cor) e dá uma ferramenta de coleta de dataset rotulado para continuar a
alimentar `images/digitos_hud/`. O problema 2 continua por resolver — ver
nota no próprio ficheiro.

**Antes de aplicar o stash e commitar para produção:** portar a busca
dinâmica desse tester para `supercruise_assist.ler_distancia_hud()` (ou
substituí-la por ela), validar ao vivo durante uma aproximação real (não só
contra screenshots estáticos), e só depois ligar `docking.py` a isto.
