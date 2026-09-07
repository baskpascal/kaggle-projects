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
- `opponents/frozen/`: snapshots anteriores à correção de venda, com parâmetros e hashes.
- `opponents/public/`: adversário externo fixado por commit, com licença preservada.
- `docs/`: revisão do projeto e evidências da correção da issue #3.

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

`config.yaml` documenta as faixas; o CLI não aplica automaticamente esse arquivo:

1. Desenvolvimento: `1000:1100`, com ajustes permitidos.
2. Validação: `100000:100200`, após congelar um candidato e seus parâmetros.
3. Holdout: começa em `9000000`, reservado para a decisão final de lançamento.

As faixas são semiabertas. O relatório da issue #3 registra o uso de
`100000:100100`; esses 100 seeds já foram vistos e não constituem uma futura
validação inédita. Não ajuste parâmetros com base neles e os reutilize como teste
independente. Use pelo menos 100 blocos na validação, examine cada adversário
separadamente e mantenha o holdout intocado até a decisão final.

Não promova apenas por margem média ou vitória contra variantes próprias. Compare
taxa de vitória pareada, falhas, sobras, descarte e orçamento de execução. O intervalo
bootstrap descreve os seeds e adversários testados; não estima a posição no ranking.

## Artefato de submissão

```bash
.venv/bin/python -m submission.build --output submission/dist/main.py
.venv/bin/python -m submission.preflight submission/dist/main.py \
  --output submission/dist/preflight.json
```

O build também gera `.tar.gz` com `main.py` na raiz e um manifesto com hashes.
Recusa sobrescrever um artefato diferente. O preflight joga uma temporada completa
pelo loader oficial. Nenhum desses comandos envia uma submissão ao Kaggle.
