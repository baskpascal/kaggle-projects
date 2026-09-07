# Experimentos A e B — `planned` (#7) e `FERTILIZE` (#6)

Issue #15, P0 e P1.

**Resultado: `FERTILIZE` promovido (v002). `planned` rejeitado.**

Antes dos resultados, o achado metodológico que muda como ler os dois: **o campo de
oponentes de dev não discrimina**, e isso quase fez o Experimento B ser descartado por engano.

## O campo de dev é degenerado

No conjunto de 4 oponentes (`starter`, `frozen/crop`, `frozen/animal`, `cok_v10`), o baseline
tem estes resultados absolutos:

| Oponente | Score | Margem média |
|---|---:|---:|
| starter | 1.000 | +53.773 |
| frozen/crop | 1.000 | +46.563 |
| frozen/animal | 0.995 | +10.286 |
| cok_v10 | 0.000 | −111.262 |

Três dos quatro confrontos são decididos por margens de dezenas de milhares. Nenhuma mudança
realista os inverte. Sobra **um único confronto vivo** (`animal`), e o score agregado passa a
medir apenas "essa partida virou ou não". Uma melhoria uniforme de +1.000 a +2.000 de margem
é invisível nessa métrica, e uma mudança que só quebre o `animal` parece catastrófica.

A validação final por isso usa **7 oponentes**, incluindo os congelados `baseline`,
`diversified` e `sheep`, que produzem confrontos realmente equilibrados (base 0.445, 0.595 e
0.400). É o regime que importa: no ladder do Kaggle o matchmaking pareia por rating, ou seja,
partidas apertadas.

## Experimento A — `planned` (#7): rejeitado

`score_crops` foi dividido em `crop_context` (varredura das fazendas, uma vez por turno) e
`score_crops` (aritmética pura), para o planner poder re-pontuar a cada tile comprometido sem
varrer tudo de novo. O planner passou a escolher o cultivo por tile e a comprar semente para a
mistura efetivamente planejada — sem isso ele planeja tiles para os quais não tem semente.

Dev `1000:1100`, 4 oponentes, 800 partidas:

| Métrica | Delta pareado | CI95 |
|---|---:|---|
| Score | **−0.2487** | [−0.2500, −0.2462] |
| Dinheiro | −2.843 | [−3.538, −2.123] |

Não é ruído de confronto apertado: contra `frozen/animal` a mudança custa **−15.768 de
dinheiro e −27.366 de margem**. É colapso econômico real naquele confronto.

O mecanismo é visível no volume vendido: melão cai de 120,8 para 108,8 unidades por partida e
morango de 126,7 para 119,1, enquanto trigo sobe de 33,4 para 41,7 e cenoura de 17,1 para
20,9. Ou seja, a previsão marginal corrigida empurra o agente **para fora dos cultivos
premium, em direção a grãos baratos**.

A explicação mais provável é um descasamento de horizonte que a correção do `planned` expõe
em vez de causar: o forecast soma `quantity` unidades por tile planejado **imediatamente**,
enquanto a produção real chega ao longo de 10+ dias e a cidade consome continuamente; a
demanda subtraída é limitada a `min(peak, days_left)`. O resultado é uma previsão
sistematicamente pessimista sobre a própria produção — e o pessimismo cresce justamente nos
cultivos de ciclo longo e alto valor.

Corrigir `planned` sem corrigir o horizonte piora o agente. O parâmetro fica desligado.

## Experimento B — `FERTILIZE` (#6): promovido

`FERTILIZE` entra como mais um job pontuado, competindo economicamente: as unidades extras são
precificadas por `sale_value` no inventário previsto e descontadas do fertilizante que se
deixa de vender. Aplicação com valor negativo nunca é enfileirada. O fertilizante é comprado
via `BUY_PRODUCT` apenas para aplicações já julgadas lucrativas.

As regras de rendimento foram espelhadas do interpretador e fixadas por teste: cultivos de
ciclo único ganham +1 por dia regado dentro de `[ceil(max_yield_day/2), max_yield_day]`,
dobrado para +2 sob fertilizante; cultivos contínuos rendem 2 em vez de 1 na produção
agendada quando regados e fertilizados. `FERTILIZE` cobre `day..day+2`.

**Validação — seeds inéditas `200000:200100`, 7 oponentes, 1.400 partidas por configuração:**

| Métrica | Delta pareado | CI95 |
|---|---:|---|
| **Score** | **+0.0800** | **[0.0657, 0.0950]** |
| Dinheiro | +640 | [410, 863] |
| Margem | +1.182 | [887, 1.484] |

Por oponente:

| Oponente | Score base | Δ score | CI95 | Δ margem |
|---|---:|---:|---|---:|
| starter | 1.000 | +0.0000 | [0, 0] | +655 |
| **frozen/baseline** | **0.445** | **+0.4700** | [0.390, 0.555] | +2.362 |
| frozen/crop | 1.000 | +0.0000 | [0, 0] | +1.137 |
| frozen/animal | 1.000 | +0.0000 | [0, 0] | +1.130 |
| **frozen/diversified** | 0.595 | +0.0950 | [0.045, 0.150] | +2.178 |
| frozen/sheep | 0.400 | −0.0050 | [−0.045, 0.035] | +865 |
| cok_v10 | 0.000 | +0.0000 | [0, 0] | −55 |

A margem melhora contra **todos** os oponentes exceto um empate estatístico com o `cok_v10`.
Onde há confronto apertado, isso vira vitória. Onde a partida já estava decidida por 50.000, o
score não se move — que é precisamente por que o campo de dev não conseguia enxergar o ganho.

Frequência: 15,7 aplicações de `FERTILIZE` por partida.

### Correção acoplada

`sale_orders` vendia o `FERTILIZER` do galpão enquanto uma unidade ainda caminhava para
buscá-lo, e o `FERTILIZE` subsequente virava no-op. Agora o fertilizante reservado é retido da
venda, no mesmo padrão do `reserve_wheat`. Ações sem efeito caíram de 4 em 800 partidas para 2
em 1.400 — resíduo de 0,14%, não zerado.

## Resposta às perguntas da #15

1. **Corrigir `planned` melhora o agente?** Não. −0.2487 de score pareado, robusto.
2. **`FERTILIZE` melhora?** Sim. +0.0800 em seeds inéditas, CI95 excluindo zero.
3. **Continuidade de execução melhora?** Não — ver `EXPERIMENT_C_CONTINUITY.md`.
4. **Alguma reduz o 0/200 contra o `cok_v10`?** **Nenhuma.** Os três deltas contra ele são
   exatamente zero. O gap não é de fertilização, previsão marginal nem roteamento.
5. **O que compõe a próxima versão?** Só `FERTILIZE`. É a v002.

## Recomendação para a V0.3

O `cok_v10` ganha por **~3,4x em dinheiro**, não por margem apertada. Três mudanças
independentes no planner não moveram esse número em nenhuma direção, o que sugere que a
diferença é estrutural — escala de operação, uso de terra ou de mão de obra — e não de
afinação de heurística. Antes de mais um experimento de planner, vale medir *onde* o dinheiro
dele aparece: comparar produção por cultivo, quadrantes comprados, contratações por dia e
receita por unidade entre os dois agentes numa mesma partida.

E qualquer experimento futuro deve usar o campo de 7 oponentes. O de 4 não consegue medir.
