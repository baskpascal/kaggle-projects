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

---

# Tier 2: o motor é o mesmo, e a faixa decisiva é decidida por 0,1%

Instrumento: `experiments/ladder_reproduction.py`. Evidência:
`docs/ladder-reproduction-v006.json`. Arquivo de replays em
`data/kaggle/ladder/replays-56125200.zip`, no mesmo formato dos dumps oficiais, de modo que
`top_panel.reproduce` e `tape_agent` o leem sem alteração.

Autenticação pela rota **pública** `GET /api/v1/competitions/episodes/{id}/replay` com
Bearer da credencial que o Kaggle CLI já mantém em `~/.kaggle` — é a rota que o próprio CLI
chama. 20 replays da faixa 2600–2800 baixados, **3.580 das 3.600 views ainda disponíveis**.

## Level A — fidelidade do motor: 20 de 20 exatos

Replayando os dois streams gravados de cada episódio na seed gravada, sem ninguém decidir
nada, o dinheiro terminal local bateu **exatamente** com o reward publicado pelo Kaggle, nos
dois backends (`fast` e `official`), com zero falhas de callback.

**Não existe discrepância de ambiente.** Nosso `kaggle-environments==1.32.7` é o deles. Esse
ramo da H1 está fechado, e todos os números locais deste repositório continuam confiáveis
como simulação — o problema não é o simulador.

## Level B — e por que ele vale menos do que parece

Agreement 20 de 20, margem local mediana idêntica à do ladder (+16,5).

Isso **não** é validação preditiva. O `v006` é determinístico: contra o stream gravado do
oponente, na mesma seed e no mesmo seat, ele refaz a própria partida do ladder. O resultado
que o Level B realmente estabelece é mais estreito e ainda assim útil: **o `v006` rodando
aqui é bit a bit o `v006` que rodou no Kaggle.** Não há divergência de artefato, de versão
nem de seat.

A validação preditiva de verdade exigiria o oponente reativo, que não temos — um tape fixo
deixa de responder assim que divergimos dele. Registrado aqui para que o número 1,0 não seja
lido como o que não é.

## O achado: a escala das margens na faixa que decide a corrida

As 20 partidas contra oponentes de 2600–2800, por margem:

    -1.119  -1.099   -350    -93     -5     -2     -2     +3     +5    +15
       +18    +25    +55   +111   +155   +225   +679   +745  +3.983 +5.115

    |margem| mediana        102 moedas
    dinheiro típico          91.813
    |margem| como fração          0,111%
    decididas por < 100 moedas   10 de 20
    decididas por < 1.000        16 de 20

**Metade das partidas na faixa decisiva é decidida por menos de 100 moedas em 92.000.** O
painel local mede o mesmo agente contra margens de ±81.000 — uma régua 800 vezes mais grossa
do que a diferença que o ladder está de fato medindo.

Advertência sobre esta amostra: os 20 episódios são os **mais antigos** dos 67 da faixa, não
uma amostra aleatória. Eles somam 13 vitórias em 20, contra 0,388 no conjunto dos 67. A
distribuição de margens é o achado; a win rate desta amostra não é representativa e não deve
ser citada como tal.

## O que a H1 virou

`H1: local_eval is conditionally biased relative to live ladder` sobrevive, mas com dois
ramos eliminados por medição:

- **não é ambiente** — Level A, 20/20 exatos;
- **não é execução nem seat** — tier 1, 204/204 `COMPLETED`, 0,593 contra 0,594.

O que resta é o **setup de avaliação**: quais adversários o painel contém, e em que escala
ele mede. E a segunda parte agora tem um número. Uma função objetivo calibrada em 80.000 não
tem resolução para escolher entre agentes separados por 102 moedas.

## Próximo

Reconstruir o evaluator em torno da escala real: adversários na faixa 2600–2800 e um
objetivo que resolva centenas de moedas, não dezenas de milhares. Só depois disso volta a
fazer sentido gastar CPU em busca, GA, option selector ou RL.
