# Kaggriculture lab

Laboratório de agentes para a [competição Kaggriculture](https://www.kaggle.com/competitions/kaggriculture).
O objetivo é vencer partidas pelo dinheiro final. Receita e margem ajudam a
diagnosticar estratégias; não substituem taxa de vitória contra adversários fortes.

## Instalação e testes

Requer Python 3.12 ou superior. A partir desta pasta:

```bash
bash scripts/setup.sh
.venv/bin/python -m pytest -q
```

Para o harness distribuído opcional, use `bash scripts/setup.sh --distributed`. A
dependência Ray não faz parte do artefato de submissão. A configuração privada, os gates
entre máquinas e o benchmark estão em [RAY_CLUSTER.md](docs/RAY_CLUSTER.md).
Depois da configuração única, `python3 scripts/ray_cluster.py ensure` mantém o papel deste
PC e o serviço Ray ativos automaticamente; agentes de IA recebem essa regra pelo
`AGENTS.md` da raiz.

O ambiente é `kaggle-environments==1.32.7`, com SHA-256 do interpretador validado
em `arena/engine.py`. As dependências mínimas ficam em `requirements.lock`.
O ambiente oficial é instalado com `--no-deps` para evitar dependências de outros
jogos. Mensagens de importação de jogos opcionais não representam falha deste jogo.
Uma divergência no fingerprint exige rever o simulador, a estratégia e os testes;
atualizar somente o hash não valida a mudança.

## Organização

- `agent/`: observações, previsão econômica, seleção de tarefas e venda marginal.
- `arena/`: execução com interpretador oficial, adversários e ligas nos dois assentos.
- `eval/`: métricas, intervalos por blocos de seeds e comparação pareada.
- `submission/`: geração de Python independente e teste pelo loader real do Kaggle.
- `versions/`: artefatos imutáveis; `champion` aponta para `v000`, `challenger` usa o código atual.
  `v003` e `v004` não são gerados por `submission/build.py`: são portfólios de fitas construídos
  por `experiments/tape_portfolio.py` e `experiments/chassis_portfolio.py`, respectivamente, com a proveniência das fitas no seu manifesto.
- `opponents/frozen/`: snapshots anteriores à correção de venda, com parâmetros e hashes.
- `opponents/public/`: adversários externos fixados por commit ou por notebook público, com licença preservada.
- `experiments/episode_tapes.py`, `tape_screen.py`, `tape_portfolio.py`: ler o dump diário de
  episódios do topo da liga, medir as fitas na nossa arena e emitir um portfólio roteado.
- `docs/`: revisão do projeto, evidências da issue #3 e `LADDER_META.md`, que mede o formato
  do campo público e substitui a direção proposta em `TAPE_ROUTING_FINDING.md`.
  `CHASSIS_FINDING.md` mede o chassis reativo sobre fitas idênticas e
  `YHAY_ROUTER_FINDING.md` mede o eixo maior que os dois: a procedência da fita.

Os nomes `crop`, `animal` e `diversified` são variantes do planner **atual**.
Para comparar versões, use os arquivos congelados como adversários: do contrário,
uma alteração na estratégia muda os dois lados da comparação.

## Partida e liga

```bash
.venv/bin/python -m arena.match --agent challenger \
  --opponent opponents/public/cok_v10/main.py --seed 1000 \
  --replay replays/current-vs-public.json

.venv/bin/python -m arena.league --candidate versions/v001/main.py \
  --opponents starter,opponents/frozen/crop/main.py,opponents/frozen/animal/main.py,opponents/public/cok_v10/main.py \
  --seeds 1000:1010 --workers 4 --output experiments/results/dev-example
```

Cada seed é jogada nos dois assentos contra todos os adversários. `fast` usa o
interpretador oficial sem o custo do wrapper; os testes comparam os replays completos
com `--backend official`. Falhas de callback invalidam uma comparação de estratégias.
`matches.jsonl` preserva resultados, configuração, hashes, auditoria e tempos de cada
callback; `summary.json`, `report.md` e `dashboard.html` facilitam a leitura.

Rode o baseline e o candidato com os mesmos adversários, seeds e backend, depois:

```bash
.venv/bin/python -m eval.compare experiments/results/baseline \
  experiments/results/candidate --output experiments/results/comparison.json
```

A comparação rejeita partidas ausentes/duplicadas, adversários alterados, mistura
de candidatos, configurações diferentes e falhas de execução. Receita por unidade
usa vendas realmente executadas, não ordens solicitadas. Produto sem vendas fica
sem estimativa. `unsold_items` conta galpão e inventários ao final; `overflow_items`
conta descarte em entregas manuais e noturnas, que antes ficava invisível.

## Disciplina de avaliação

`seed_registry.json` é a autoridade sobre seeds e `arena/seeds.py` o aplica: a
liga recusa qualquer seed fora do registro, com a classificação errada ou dentro
do holdout. `config.yaml` e este README apenas resumem o registro.

1. `diagnostic`: `0:1000` e `314159`, para testes e preflight. Nunca é evidência
   competitiva.
2. `dev`: `1000:1100`, com ajustes permitidos.
3. `seen`: `100000:100125`, já consumidos pela issue #3 e pelo experimento de
   FERTILIZE. Não são validação inédita: não ajuste parâmetros neles e depois os
   reutilize como teste independente.
4. `validation`: `100125:100200`, reservados para um candidato já congelado.
5. `holdout`: começa em `9000000`, reservado para a decisão final de lançamento.

As faixas são semiabertas. Rodar `--split validation` queima os seeds pedidos
*antes* da primeira callback e os reclassifica como `seen`, sob trava exclusiva.
Uma execução interrompida ou fracassada não os devolve: essa é a diferença entre
validação inédita e evidência já vista. O `summary.json` registra a revisão e o
sha256 do registro usados.

A reserva atual contém 75 seeds inéditas. Uma validação de 100 blocos exige nova
faixa registrada com proveniência; não reutilize `100100:100125`. Veja o driver
pareado e a política em [PAIRED_EVALUATION.md](docs/PAIRED_EVALUATION.md).

Use pelo menos 100 blocos na validação, examine cada adversário separadamente e
mantenha o holdout intocado até a decisão final.

`docs/ISSUE22_DAILY_ECONOMICS.md` traz o primeiro diagnóstico feito com ela: o
déficit contra o adversário público é de liquidez inicial, não de execução.

Para executar as duas pernas com um comando, use `python -m arena.paired`.
Ele declara os dois candidatos no registro antes das partidas, congela os ratings,
produz o bootstrap pareado e executa o gate. `--paired-with` de `arena.league`
continua disponível para declarar lotes manualmente. Comandos completos estão em
[PAIRED_EVALUATION.md](docs/PAIRED_EVALUATION.md).

Cada partida também emite telemetria diária (`daily.csv`, `summary.json`): caixa,
primeira receita, piso de caixa, contratações, plantios, colheitas, preço
realizado e o destino de cada ordem de mercado. Os agregados diários são
conferidos contra os totais auditados da partida; uma divergência aparece como
`RECONCILIATION FAILED` no `report.md`.

Não promova apenas por margem média ou vitória contra variantes próprias. Compare
taxa de vitória pareada, falhas, sobras, descarte e orçamento de execução. O intervalo
bootstrap descreve os seeds e adversários testados; não estima a posição no ranking.

## RunSpec e manifesto de evidência

```bash
.venv/bin/python -m arena.runspec experiments/results/<run>/spec.json \
  --evidence standing=<...>.json --output <run>/manifest.json
```

Um run é um `RunSpec` validado antes de qualquer consumo e endereçado pelo que ele mediu.
Entram no `run_id` os agentes e oponentes com hash e lineage, o painel, seeds/split/revisão
do registro, backend e fingerprint do motor, deadline e política de retry, thresholds e o
gate que vai ler o resultado. **Não** entra a distribuição (topologia, workers, batch size):
a #43 mediu lotes bit-exatos entre nós, então um run começado local e retomado no cluster é
o mesmo run.

`arena.paired` escreve `spec.json` e `manifest.json`, carimba `run_id` na comparação e no
gate, e o store de jobs passa a pertencer a um run — `--resume` recusa servir jogos a um
spec diferente, que é o buraco que `job_id` sozinho não enxergava. O manifesto responde
`BOUND`, `REFUSED` (peça de outro run, nomeada) ou `PARTIALLY BOUND` (peça anterior aos
specs, nunca assumida como amarrada). Detalhes em [RUN_SPEC.md](docs/RUN_SPEC.md).

## Corpus público externo

Antes de usar qualquer dataset público como evidência, leia
[EXTERNAL_CORPUS.md](docs/EXTERNAL_CORPUS.md). Resumo: o ladder virou para `1.32.7` em
2026-08-15 e **41% do corpus público é de balanceamentos anteriores**, então
`engine_version == 1.32.7` é gate obrigatório. Em particular,
`destbreso/kaggriculture-benchmark-matchups` é template metodológico e **não** ground truth
utilizável no motor atual: nenhuma das 421 linhas de `matchups_top.parquet` foi gravada sob
1.32.7.

## Painel meta versionado

```bash
.venv/bin/python -m eval.panel            # o que as fontes atuais produzem
.venv/bin/python -m eval.panel --write    # fila a revisão em opponents/panels/
```

O painel é um dataset versionado, não um `--ranks` digitado ao lado da execução. Cada
revisão junta o artefato fixado (hash, lineage, motor), o rating datado de
`opponents/ratings.json` e o rank datado de `opponents/panel-observations.json`, e hasheia
o conjunto em uma `revision` imutável. Um rank `author_upper_bound` nunca coloca um
oponente *dentro* de uma faixa: ele limita o artefato por cima e só pode provar que um
agente não é top 10.

O gate recusa painel vencido (7 dias), faixa decisiva vazia, faixa carregada por uma só
lineage e concentração acima de 50% — sempre recusando, nunca aprovando por omissão.
`eval.standing --panel <revisão>` deriva ranks e lineages da revisão e registra no
relatório contra o que ele ficou de pé; `--panel` e `--ranks` são mutuamente exclusivos.

Hoje o painel **falha**: o melhor artefato público fixado está em rank 45, as faixas
`top10` e `rank10_30` estão vazias e nenhum standing sobre ele autoriza um envio. Detalhes
e procedimento de refresh em [PANEL_SNAPSHOT.md](docs/PANEL_SNAPSHOT.md).

## Artefato de submissão

```bash
.venv/bin/python -m submission.build --output submission/dist/main.py
.venv/bin/python -m submission.preflight submission/dist/main.py \
  --output submission/dist/preflight.json
```

O build também gera `.tar.gz` com `main.py` na raiz e um manifesto com hashes.
Recusa sobrescrever um artefato diferente. O preflight joga uma temporada completa
pelo loader oficial. Nenhum desses comandos envia uma submissão ao Kaggle.

## Dossiê de release

As quatro perguntas de um envio já tinham resposta em relatórios separados: o gate
pareado contra o incumbente, o standing absoluto contra o meta, o preflight do arquivo
exato e a política dos dois slots ativos. O risco estava na passagem manual entre eles —
uma comparação sobre um artefato citada ao lado de um preflight de outro.

```bash
.venv/bin/python -m eval.dossier \
  --slots docs/active-slots.json \
  --candidate v009:A \
  --comparison experiments/results/v009-vs-incumbent.json \
  --standing experiments/results/v009-standing.json \
  --preflight versions/v009/preflight.json \
  --output docs/release-dossier.json
```

`eval.dossier` recusa antes de avaliar: se o hash avaliado, o hash do standing e o
sha256 do preflight não forem o mesmo, o veredito é `NO DECISION` e nenhum número
embaixo disso conserta. Nomear o slot deslocado basta — o incumbente é derivado da
declaração, e é ele que `eval.submit_gate` confere contra a perna baseline que rodou.
Somente leitura: não envia nada e não lê a conta do Kaggle.
Veja [RELEASE_DOSSIER.md](docs/RELEASE_DOSSIER.md).

## Solver de blocos e política final

[BLOCK_SOLVER.md](docs/BLOCK_SOLVER.md) descreve a primeira busca local de blocos,
seu comando e o resultado negativo inicial. É uma entrega parcial da #39.
[FINAL_SUBMISSION_POLICY.md](docs/FINAL_SUBMISSION_POLICY.md) confirma as regras
com respostas diretas dos hosts: episódios de toda a competição podem contar,
desde que ambos os agentes continuem ativos. A #37 não depende mais de relato
indireto nem da interpretação de avaliação somente após o prazo.
[RELEASE_DOSSIER.md](docs/RELEASE_DOSSIER.md) descreve o veredito único de release
da #47, que junta esses relatórios e recusa combinações que não falam do mesmo
artefato.
