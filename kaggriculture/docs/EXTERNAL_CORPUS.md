# O corpus público: o que dá para usar, e o que não dá

Levantamento de 2026-09-09 sobre os datasets públicos da competição. Registra uma **decisão
arquitetural** que vale para todos os terminais, não só uma lista de fontes.

> **Decisão.** `destbreso/kaggriculture-benchmark-matchups` é **template metodológico, não
> ground truth utilizável no motor atual.** Os banks gravados ali foram medidos sob
> `1.32.6` e não reproduzem sob o `1.32.7` que `arena/engine.py` fixa. Quem reutilizar
> aqueles números como baseline reintroduz um erro de medição.

## A versão do motor divide o corpus em dois

O ladder virou para `1.32.7` em 2026-08-15. Contado em `episode_features.csv` do
`georgymamarin/kaggriculture-episodes` (189.738 episode-seats):

| engine | seats |
|---|---:|
| **1.32.7** | **111.432** |
| 1.32.6 | 54.908 |
| 1.32.4 | 10.592 |
| 1.32.2 | 7.278 |
| 1.32.5 | 4.612 |
| 1.32.3 | 916 |

**41% do corpus é outro balanceamento.** `experiments/episode_tapes.py` grava `engine` por
assento e não filtra, então uma library regenerada hoje sem hard gate mistura dois jogos e
produz números que parecem válidos. Essa é a issue **#52**, e `engine_version == 1.32.7` é
gate obrigatório, não filtro opcional.

O `matchups_top.parquet` (421 linhas, o arquivo que o próprio README chama de "start here")
é o caso concreto: 265 linhas em `1.32.6`, 120 em `1.32.4`, 35 em `1.32.5`, 1 em `1.32.3`,
**nenhuma** em `1.32.7`. O `engine_fixture.json` dele declara a versão abertamente. O que
aproveitamos dali é o **método** — comparação pareada contra recording, com semente
resolvida, assento e stream completo do adversário — e o formato de `exact_replay.json`
como modelo da nossa própria prova de reprodução, reconstruída sob 1.32.7.

## As fontes, por utilidade

| dataset | votos | para que serve aqui |
|---|---:|---|
| `georgymamarin/kaggriculture-episodes` | 61 | **primária.** Os CSVs leves respondem quase tudo sem tocar nos 12 GB de parquet: `episodes.csv` (rating por assento), `episode_features.csv` (`engine_version`), `stream_hashes.csv`, `per_submission_coverage.csv`, `teams.csv`. Reescrito diariamente, então a revisão precisa ser pinada |
| `kaggle/kaggriculture-episodes-index` | 27 | 1,5 KB. Manifesto diário oficial: 41 dias com `episode_count`, `top_avg_score`, `median_avg_score`. Fonte barata de freshness |
| `kaggle/kaggriculture-episodes-<data>` | ~22 | raw autoritativo (~500 MB/dia, ~660 episódios). Só a partir de 2026-08-15 vale como evidência |
| `raykkretzschmar/kaggriculture-reference-agents` | 33 | 10 agentes de referência com fonte + `head_to_head_games.csv`. Lineages de banda média e oponentes de sanidade, não das faixas decisivas |
| `destbreso/kaggriculture-benchmark-matchups` | 22 | método e fixtures. **Não** os banks — ver a decisão no topo |
| `vijaikm/kaggriculture-match-replay-corpus` | 6 | superseded por `episode_features.csv` |

## O campo forte cabe em poucas aberturas

Só episódios públicos em `1.32.7`; comportamento = hash exato dos 24 primeiros turnos
(`stream_h24`), com os horizontes de informação do motor em 48 e 136:

| banda | seats | subs | h24 | h48 | h136 |
|---|---:|---:|---:|---:|---:|
| ≥2800 | 119 | 66 | **15** | 17 | 44 |
| 2700–2800 | 582 | 200 | 23 | 29 | 71 |
| 2600–2700 | 948 | 355 | 70 | 84 | 147 |
| 2500–2600 | 1806 | 697 | 129 | 163 | 313 |
| 2300–2500 | 7729 | 2385 | 320 | 437 | 865 |

Duas consequências:

- **Para o painel (#45).** Os pisos de `MIN_LINEAGES_PER_BAND = 2` e `MAX_LINEAGE_SHARE =
  .5` não são conservadores demais: perto do topo há pouca coisa para escolher. Na janela
  de freshness de 7 dias o corpus oferece 27 assentos ≥2700 sobre 11 aberturas distintas, e
  3 assentos ≥2800.
- **Para a #39.** Em vez de aprender "todo o espaço de agentes", o alvo é um conjunto
  **enumerável** de 15–32 famílias de abertura fortes, e a pergunta vira onde elas bifurcam
  em h48/h136.

## O corpus não alcança o topo do ladder

Rating máximo observado em `1.32.7`: **2953,3**. Zero assentos acima de 3000, cinco acima
de 2900. O crawl é enviesado por submissão — `per_submission_coverage.csv` dá o fator
exato, mediana 1,0 mas p10 = 0,0 — e prioriza o terço mais fresco das submissões
conhecidas.

Ou seja: ingerir o corpus **não** preenche sozinho as faixas `top10` e `rank10_30` do
painel. Elas continuam vazias e o painel continua `FAIL` até um artefato de topo ser
fixado. Ingestão não pode preencher buraco inventando dado; é o critério 9 da #52.

Reponderar por cobertura também não é `1 / coverage`: coverage perto de zero vira bomba
estatística e coverage 0 não ganha peso infinito. Peso capado, e `effective_sample_size`
reportado junto de qualquer agregado que dependa dele.

## Lineage é hierarquia, não um hash

`stream_h24` identifica o **agente** e é ótimo para dedup e clone exato; `stream_h719`
identifica o **episódio**, não o agente. Mas exatidão de 24 turnos fragmenta duas políticas
praticamente iguais por causa de uma ação, então lineage se guarda em camadas —
`lineage_h24`, `lineage_h48`, `lineage_h136`, `stream_full_hash` — e mede clone cedo,
família até o horizonte de informação e divergência posterior, em vez de família escrita em
prosa no manifest do oponente.

## Ver também

- `docs/PANEL_SNAPSHOT.md` — o painel como dataset versionado (#45)
- `docs/RUN_SPEC.md` — por que fingerprint de motor é campo material do `run_id` (#46)
- `docs/LADDER_META.md` — o que a discussão da competição estabelece
- issue **#52** — a ingestão que este levantamento motiva
