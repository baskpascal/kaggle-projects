# Issue #3 — venda marginal e avaliação

## Resultado

A venda agora considera o preço marginal, recuperação de demanda, capacidade do
galpão, inventários em trânsito, reserva de trigo e fim da temporada. O estoque
retido também entra na previsão de plantio para evitar amplificar a superprodução.
O candidato foi congelado em `versions/v001/main.py`, SHA-256
`ef31ae632865df5374309cee907dbd1109374ef42f7d1a2946b12f3bafbdabbf`.
`champion` continua apontando para v000; nenhuma submissão foi enviada ao Kaggle.

## Validação principal

100 seeds inéditos em relação ao desenvolvimento (`100000:100100`), dois
assentos, quatro adversários: starter, crop congelado, animal congelado e
`cok_v10`. São **800 partidas por versão, 1.600 no total**. O baseline foi
congelado antes de qualquer alteração deste trabalho. Os dois lados usam os
mesmos hashes de adversários, configuração e interpretador `1.32.7`.

| Métrica | Baseline | Candidato |
|---|---:|---:|
| Receita/unidade de MELON | $185,66 | $192,67 (+3,78%) |
| Receita/unidade de STRAWBERRY | $231,77 | $241,38 (+4,15%) |
| Taxa de vitória | 74,75% | 75,00% |
| Sobras médias | 0 | 0 |
| Itens descartados | 0 | 0 |
| Falhas no conjunto após repetições documentadas | 0 | 0 |

Dinheiro final: **+$2.257,47 por partida**, IC95 pareado [$1.824,30; $2.756,75].
Margem contra o adversário: +$1.647,28, IC95 [$991,17; $2.278,42].
Ganho de vitórias: +0,25 ponto percentual, IC95 [0; 0,625]; não demonstra
superioridade ampla em taxa de vitória.

O IC95 da melhora de receita/unidade é [$6,14; $7,91] para melão e
[$7,23; $11,96] para morango. O bootstrap reamostra blocos completos de seeds,
preservando assentos e adversários, e calcula a razão receita/volume em cada
reamostragem. Esses efeitos incluem a correção de previsão de estoque; não são
uma estimativa isolada de um parâmetro de venda mantendo toda produção fixa.

| Adversário | Vitórias do baseline | Vitórias do candidato |
|---|---:|---:|
| starter | 200/200 | 200/200 |
| crop congelado | 200/200 | 200/200 |
| animal congelado | 198/200 | 200/200 |
| cok_v10 público | 0/200 | 0/200 |

A margem contra o público piorou de -$110.259 para -$111.187. A melhora média
da liga não deve esconder isso. Esta correção melhora o agente padrão, mas não
o transforma em candidato a ganhar a competição.

## Lã: avaliação separada e limite da evidência

O agente padrão não produz lã. Testá-la exige uma variante explícita: seis
ovelhas, nove trabalhadores, demais parâmetros iguais aos de cada versão.
Foram jogadas outras **800 partidas**: 100 seeds, dois assentos, starter e
cok_v10, 400 partidas por versão.

| Métrica | Baseline com ovelhas | Candidato com ovelhas |
|---|---:|---:|
| Receita/unidade de WOOL | $155,13 | $155,69 (+0,36%) |
| Receita/unidade de MELON | $174,17 | $190,59 |
| Receita/unidade de STRAWBERRY | $224,33 | $225,12 |
| Taxa de vitória | 50% | 50% |
| Sobras / descartes / falhas | 0 / 0 / 0 | 0 / 0 / 0 |

A média de lã satisfaz o aceite numérico da issue, mas o **IC95 da diferença
é [-$7,29; $8,48]**, portanto o ganho não é robusto. Nos dez seeds de
desenvolvimento, houve regressão de $167,29 para $157,09. A validação usa uma
amostra maior e separada; os resultados negativos de desenvolvimento foram
preservados, não descartados.

Na variante de ovelhas, dinheiro final aumentou $905,42 em média, com intervalo
que inclui zero; a margem contra os adversários piorou $1.677,80, IC95
[-$3.290,32; -$133,39]. **Não promover esta variante** com base no preço médio
de lã. O alvo de animais permanece zero no agente padrão.

## Desenvolvimento, estabilidade e rastreabilidade

- Desenvolvimento principal: 80 jogos por versão, seeds `1000:1010`.
- Contra diversified congelado: mais 20 jogos por versão; vitórias de 45% para
  55%, com amostra insuficiente para conclusão forte.
- A primeira estratégia de retenção, antes de incluir estoque no plantio,
  piorou o preço do melão. O resultado orientou a correção conjunta da #12.
- Foram experimentadas previsões de abastecimento por rebanhos nos seeds de
  desenvolvimento. Não melhoraram a evidência; não estão no código entregue.
- Na primeira execução concorrente da validação principal ocorreram três
  timeouts: baseline seed 100086/assento 1/starter; candidato
  100018/assento 1/starter; adversário animal de 100087/assento 1.
  Todos nos passos 0 ou 2, durante concorrência elevada. Foram repetidas as
  três chaves **em ambas as versões**, sequencialmente, sem mudar timeout,
  seed ou estratégia. As seis repetições passaram. A hipótese é contenção da
  máquina; os relatórios originais com falhas foram preservados.
- p99 do callback do candidato na liga principal: 8,82 ms. O máximo chegou a
  891,85 ms mesmo após as repetições, indicando que o tempo de parede desta
  máquina sob carga não é um perfil limpo de custo computacional.

As comparações completas estão em [issue3-validation.json](issue3-validation.json)
e [issue3-sheep-validation.json](issue3-sheep-validation.json). Metadados, hashes,
sumários originais e registro de repetições estão em
[issue3-provenance.json](issue3-provenance.json). Os logs por partida permanecem
localmente em `experiments/results/issue3-*`; são ignorados pelo Git devido ao
volume de tempos de callback. A revisão geral está em
[PROJECT_REVIEW.md](PROJECT_REVIEW.md).

## Reproduzir

A partir da pasta `kaggriculture`, após instalar conforme o README:

```bash
.venv/bin/python -m arena.league --candidate opponents/frozen/baseline/main.py \
  --opponents starter,opponents/frozen/crop/main.py,opponents/frozen/animal/main.py,opponents/public/cok_v10/main.py \
  --seeds 100000:100100 --workers 4 --split validation --output experiments/results/repro-baseline
.venv/bin/python -m arena.league --candidate versions/v001/main.py \
  --opponents starter,opponents/frozen/crop/main.py,opponents/frozen/animal/main.py,opponents/public/cok_v10/main.py \
  --seeds 100000:100100 --workers 4 --split validation --output experiments/results/repro-candidate
.venv/bin/python -m eval.compare experiments/results/repro-baseline \
  experiments/results/repro-candidate --output experiments/results/repro-comparison.json
```

Para lã, use `opponents/frozen/sheep/main.py` e `versions/v001/sheep/main.py`,
com adversários `starter,opponents/public/cok_v10/main.py`, a mesma faixa e
diretórios de saída distintos. Esses arquivos têm os mesmos hashes dos caminhos
temporários `experiments/searches/sheep-baseline/main.py` e
`experiments/searches/sheep-final/main.py` registrados nos experimentos originais.

Não execute várias ligas simultâneas em uma máquina saturada. Comparações
rejeitam falhas de callbacks; não interprete vitória por timeout como evidência
estratégica. Estes seeds já foram vistos e agora servem para reprodução, não
para uma nova validação independente. O holdout a partir de 9000000 segue intocado.

## Verificação funcional

30 testes cobrem preços e overrides, piso de $1, retenção de premium,
liquidação, venda de entregas no último turno, reserva de pickups e trigo,
capacidade e limite de ordens, estoque na previsão, lojas duplicadas, auditoria
de transações e descartes, rejeição de comparações inválidas, bootstrap,
paridade fast/oficial e build determinístico. O preflight de v001 passou pelo
loader real: 720 estados, 719 ações, ambos os agentes DONE.
