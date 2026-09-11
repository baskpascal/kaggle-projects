# O evaluator reconstruído na escala que decide o rating

Instrumento: `experiments/ladder_cohort.py`. Cohort: os 67 mundos em que o `v006` enfrentou
oponentes de 2600–2800 no ladder, reconstruídos a partir dos replays arquivados em
`data/kaggle/ladder/replays-56125200.zip`.

## Por que o painel antigo não podia servir

`eval/panel.py` organiza os adversários por **rank de leaderboard** — top10, rank10_30,
rank30_100 — o que era a pergunta certa enquanto o objetivo estava escrito como "vencer o
topo do quadro". O ladder não funciona assim. Ele pareia por **proximidade de rating**, e
`LADDER_GROUND_TRUTH.md` mediu a consequência: em 204 episódios reais o `v006` nunca
enfrentou ninguém acima de 2800. A faixa que decide o nosso rating é a vizinha, não o topo,
e o painel praticamente não a contém.

A segunda metade do mesmo problema é a régua. Na faixa 2600–2800 a margem absoluta mediana
é de 102 moedas sobre 91.813 de dinheiro típico — 0,111% — e metade das partidas é decidida
por menos de 100 moedas. Todos os objetivos que este repositório otimizou mexem em margens
de dezenas de milhares. É uma régua cerca de 800 vezes grossa demais para enxergar o que o
ladder está resolvendo.

## O que o cohort é

Um mundo por episódio da faixa, carregando o que o ladder de fato registrou: a seed do
próprio episódio, o assento que ocupamos, o stream de ações gravado do adversário como
replayer, e o resultado real. Um candidato é pontuado exatamente nesses mundos.

## O que ele não é, e isso importa

`ladder_reproduction.py` estabeleceu que o incumbente reproduz o resultado do ladder nesses
mundos **exatamente** — ele é determinístico e o stream do adversário é fixo. Duas
consequências:

1. O cohort é **calibrado por construção** para o incumbente. Que o `v006` marque aqui o
   mesmo que marcou no ladder é uma verificação de encanamento, não uma validação.
2. O cohort **não prevê rating** para um candidato alterado. No instante em que o candidato
   diverge, as ações gravadas do adversário deixam de ser resposta ao mundo à frente dele.

O que ele mede, e nada mais aqui media, é **quais partidas reais uma mudança vira, e em que
direção**, nas margens em que essas partidas foram de fato decididas.

## O número de manchete

Não é margem média — nessa escala a média é ruído mais dois blowouts. É **mundos ganhos
menos mundos regredidos** contra o resultado real do ladder, com a lista dos episódios de
cada lado, e a win rate restrita aos mundos decididos por menos de 1.000 moedas.

## Calibração: exata

`v006` sobre os 67 mundos, backend `official`, zero falhas:

    win rate local     0,3880597
    win rate do ladder 0,3880597
    ganhos 0 | regredidos 0 | net 0
    margem mediana local -95,0   contra -95 do ladder

O encanamento está certo: o cohort devolve o ladder moeda por moeda. Como dito acima, isso
era esperado por construção e não valida nada — mas se não tivesse batido, seria bug.

Intervalo bootstrap por bloco de seed sobre 67 mundos: **[0,269, 0,507]**. O limite superior
fica abaixo de 0,5, então o déficit nesta faixa é real e não é ruído de amostra.

## O que a régua nova revelou de imediato

Separando os mundos pela margem com que o ladder de fato os decidiu:

| população | mundos | win rate | \|margem\| mediana |
|---|---:|---:|---:|
| decididos por < 1.000 | 40 | **0,550** | 124 |
| decididos por ≥ 1.000 | 27 | **0,148** | 2.510 |

**Nós não estamos perdendo os cara-e-coroa.** Nos 40 mundos apertados ficamos acima de 0,5.
O déficit inteiro está nos 27 mundos em que a partida é realmente decidida, e lá perdemos
**23 de 27**.

As derrotas largas não são um modo catastrófico único: a mediana é de −1.932 e só uma passa
de −10.000 (−35.313). E não são um adversário só — as 23 derrotas se espalham por times
distintos, nenhum com mais de duas.

Aritmética do alvo: hoje são 26 vitórias em 67. Virar 12 dos 23 mundos de derrota larga leva
o cohort a 38/67 = 0,567, que é o outro lado do equilíbrio.

## Próximo

O alvo deixou de ser "melhorar o agente" e passou a ser uma pergunta respondível: **o que
acontece nos 23 mundos que perdemos por mais de mil moedas, e não acontece nos 40 apertados?**
Os episódios estão nomeados em `docs/ladder-cohort-v006.json`, e os instrumentos de fronteira
de decisão (`experiments/elite_events.py`, `experiments/planner_milestones.py`) já leem
exatamente esse formato de replay.
