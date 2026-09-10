# O que o ladder diz sobre o v006, e o que o painel local não podia dizer

Instrumento: `experiments/ladder_ground_truth.py`. Evidência: `docs/ladder-ground-truth-v006.json`,
cache bruto em `data/kaggle/ladder/episodes-56125200.json`. Submissão 56125200 (`v006`),
204 episódios atribuíveis de 205, entre 2026-09-09T14:36Z e 2026-09-10T18:32Z.

Nenhuma view de episódio foi gasta para produzir este documento. Tudo abaixo vem do
`ListEpisodes`, que é gratuito.

## A trajetória, que é o que o leaderboard público estava mostrando

    ep#  0   2026-09-09 14:36     719.3     (cold start em 600)
    ep# 20   2026-09-09 15:44   2234.3
    ep# 60   2026-09-09 17:58   2553.1
    ep#117   2026-09-10 01:20   2644.4     <- pico
    ep#140   2026-09-10 04:59   2602.0
    ep#180   2026-09-10 13:36   2549.3
    ep#204   2026-09-10 18:32   2513.3     <- 131,1 abaixo do pico, ainda caindo

A queda que os snapshots de leaderboard mostraram é real e é do próprio `v006`
(`publicLeaderboardSubmissionId` do time é 56125200). Ela não é falta de convergência: é
convergência. O agente subiu rápido demais contra um campo fraco e está sendo corrigido
para baixo desde o pico.

## Pergunta 2 primeiro: não estamos perdendo por execução

| corte | episódios | win rate | margem mediana |
|---|---:|---:|---:|
| geral | 204 | 0,593 | +54 |
| seat 0 | 108 | 0,593 | +55 |
| seat 1 | 96 | 0,594 | +40 |

- **204 de 204 episódios em `COMPLETED`.** Nenhum `ERROR`, nenhum timeout.
- **Zero episódios sem reward** para qualquer um dos lados.
- **Nenhuma assimetria de seat**: 0,593 contra 0,594, com 108 e 96 episódios.

Isso refuta, **para o `v006`**, a preocupação de que `obs["step"]` ausente no seat 1 faria o
agente repetir a lógica do turno 0. Se isso acontecesse conosco, o corte por seat seria um
desastre visível, e ele não é. A refutação vale para este agente e esta submissão; não é uma
afirmação sobre o relato original nem sobre outros agentes.

## Pergunta 1: estamos perdendo por estratégia, e o problema é estreito

| rating do oponente | episódios | win rate | margem mediana | soma dos deltas |
|---|---:|---:|---:|---:|
| < 2400 | 36 | **0,917** | +6.842 | +1.802,0 |
| 2400–2600 | 101 | 0,614 | +69 | +138,5 |
| 2600–2800 | 67 | **0,388** | −95 | −27,2 |
| > 2800 | **0** | — | — | — |

Duas leituras se impõem.

Primeira: **nunca enfrentamos ninguém acima de 2800.** O pareamento é por rating, então o
corte do top-10 (2946,6) não é um adversário que estamos perdendo — é uma faixa que ainda
não alcançamos. Todo o ganho acumulado veio da faixa abaixo de 2400, que a subida inicial
atravessou uma vez e não volta.

Segunda: o ponto onde a win rate cruza 0,5 fica entre as duas faixas centrais.
**Estimativa** por interpolação linear nos pontos médios das faixas (2500 → 0,614;
2700 → 0,388): equilíbrio em torno de **2600**. Isso é uma extrapolação de duas faixas, não
uma medição; o que está medido é que ganhamos 0,614 abaixo e 0,388 acima. Se a estimativa
estiver certa, os 2513 de agora ainda estão caindo em direção a ~2600, e a distância real
até o top-10 é da ordem de **345 pontos de força**, não os 434 que o leaderboard mostra hoje.

## O tamanho das margens, que é o achado mais surpreendente

    vitórias   n=120   margem mediana    +794
    derrotas   n= 82   margem mediana  −1.109
    geral      n=204   margem mediana     +54

Com rewards terminais na casa de 90.000 a 120.000, a partida mediana do ladder é decidida
por **menos de 1%**. O painel local mede o mesmo agente contra margens de ±81.000.

Os dois números não descrevem o mesmo jogo. Isso ainda **não** prova que o painel é um
espelho — as causas candidatas continuam abertas (distribuição de adversários, seat,
versão do engine, pareamento) e a hipótese continua na forma fraca:

> `H1: local_eval is conditionally biased relative to live ladder.`

O que a diferença de escala estabelece é mais limitado e já é suficiente para decidir
prioridade: **uma função objetivo calibrada em margens de 80.000 não tem resolução para
escolher entre agentes separados por 800.** Otimizar contra o painel não pode distinguir o
que o ladder está distinguindo.

## O que falta, e por que precisa do tier 2

O experimento que decide a H1 é reproduzir cada episódio real localmente com a mesma seed,
o mesmo seat e o mesmo adversário, e comparar `predicted_local_outcome` contra
`actual_ladder_outcome`. A seed não vem no `ListEpisodes`. Ela vem no `GetEpisode`, que
exige credencial e consome as 3.600 views por 24 horas.

`ladder_ground_truth.py` já tem o `ViewLedger` persistente, a deduplicação por
`episode_id` e o cache que nunca rebaixa histórico, para que essa coleta seja feita uma vez
só. Falta ligar a autenticação e escrever o passo de reprodução.

## Consequência para o plano

Na ordem instrumentação → causa → evaluator → otimização, a instrumentação entregou:
execução está limpa, a perda é estratégica e está concentrada na faixa 2600–2800, e o
evaluator local não tem resolução para o que decide o ladder. O próximo passo é o tier 2 e
a reprodução pareada, não mais busca, GA, option selector ou RL.
