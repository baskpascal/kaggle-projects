# O 2x2 causal do regime, o mecanismo, e por que a chave de roteamento está errada

Instrumentos: `experiments/regime_counterfactual.py` (o 2x2), `experiments/regime_library.py`
(o campo elite). Artefatos: `artifacts/regime_counterfactual_v006.json`,
`artifacts/regime_library_v006.json`, `artifacts/regime-library-rows.json`.

## A intervenção

O `router_parent.py` do `v006` decide no turno 144 (dia 6, hora 0):

```python
state.plan = SHOP_PLANS.get(tuple(shops[:2]), 0)
```

`SHOP_PLANS` tem 15 entradas e **todas contêm `YARN_STORE`**. Qualquer outro par cai no
default, plano 0 — a linha coop. As variantes forçadas trocam exatamente essa linha por
`state.plan = 3` (ovelha) ou `state.plan = 0` (coop). Todos os overlays posteriores
(`repair_weeds`, `subtract_advanced_sales`, `advance_sales`, `room_guard`, o plano terminal 2
no turno 648) ficam intactos. Três testes recusam o patch se a linha não existir, se existir
duas vezes, ou se a variante anterior sobreviver.

O ramo é verificado por **transição de estado** no turno 360, não pelo índice pedido.

## O 2x2 — CASE 3

Mesmos mundos, mesmas seeds, mesmos assentos, mesmos streams gravados dos adversários.

| contexto \ regime | ovelha/pasto | coop |
|---|---:|---:|
| **YARN cedo** (n=13) | **0,769** observado | **0,077** forçado |
| **sem YARN cedo** (n=54) | **0,185** forçado | **0,296** observado |

| | célula A (ovelha sem YARN) | célula B (coop com YARN) |
|---|---:|---:|
| W / L / T | 10 / 44 / 0 | 1 / 12 / 0 |
| ganhos vs `v006` | 6 | 1 |
| regredidos | 12 | 10 |
| net worlds | **−6** | **−9** |
| Δ margem pareada (mediana) | −9.286 | −17.564 |
| dinheiro terminal (mediana) | 84.840 (`v006`: 91.034) | 88.733 (`v006`: 104.048) |
| ramo executado | ovelha 53, coop 1 | coop 13 |
| falhas operacionais | 0 | 0 |
| build no d15 | ovelha 11, pasto 18, coop 0 | ovelha 6, vaca 8, ganso 3, coop 4 |
| estoque entrando na liquidação | 17 | 9 |

**CASE 3: interação genuína entre regime e contexto.** Ovelha domina com YARN
(0,769 contra 0,077); coop vence sem YARN (0,296 contra 0,185).

**Ressalva de magnitude.** O adversário é um tape fixo. Quando mudamos de regime ele deixa de
ser reativo, e o dinheiro dele subiu nas duas células (+1.940 e +1.988). A **direção** dos dois
efeitos é confiável; os valores de Δ margem não são precisos.

## O mecanismo: a `YARN_STORE` é o escoadouro da lã

Preço da lã no dia 15 nos 67 mundos do cohort:

| contexto | n | mediana | colapsado (≤5) |
|---|---:|---:|---:|
| sem YARN cedo | 54 | **1** | **37/54** |
| com YARN cedo | 13 | **241** | **0/13** |

Sem a loja, a lã não tem comprador e a economia ovelha produz o que não pode vender. A célula
A confirma pelo estado: build executado corretamente (ovelha 11, pasto 18, coop 0), lã em
estoque 3, dinheiro terminal 6.200 abaixo do `v006` nos mesmos mundos.

Isto **corrige** o `docs/wide-loss-diagnosis-v006.md`, que reportou o glut de lã como um
regime de mercado ubíquo atingindo 55% dos mundos sem discriminar. Os 37 de 67 são exatamente
os mundos sem loja de lã. Não é ruído de mercado, é a ausência do sink.

## A biblioteca de regimes: existe **um** regime condicionado a loja, não três

`regime_library.py` varreu os dois dumps públicos — 1.325 episódios, 2.650 assentos, zero
falhas — e ficou com os **1.264 assentos de times ≥2900**. Mediana do rebanho no dia 15,
conforme cada loja esteja ou não entre as duas primeiras:

| loja | presente | ausente | ovelha p/a | vaca p/a | ganso p/a | regime dominante |
|---|---:|---:|---|---|---|---|
| BAKERY | 311 | 953 | 6/6 | 8/8 | 3/2 | mixed_herd |
| BRUNCH_SPOT | 292 | 972 | 6/6 | 8/8 | 3/2 | mixed_herd |
| FARMERS_MARKET | 286 | 978 | 6/6 | 8/8 | 3/2 | cow_led |
| ICE_CREAM_SHOP | 302 | 962 | 6/6 | 8/8 | 2/2 | cow_led |
| PET_CAFE | 304 | 960 | 6/6 | 8/8 | 3/2 | mixed_herd |
| PIZZA_SHOP | 291 | 973 | 6/6 | 8/8 | 2/2 | cow_led |
| SMOOTHIE_SHOP | 310 | 954 | 6/6 | 8/8 | 2/2 | cow_led |
| **YARN_STORE** | **269** | **995** | **11/5** | **6/8** | **0/3** | **sheep_led** |

**Só a `YARN_STORE` muda o que a elite constrói.** As outras sete lojas deixam o rebanho
idêntico. A pergunta "para cada configuração de loja inicial, qual é a economia mais forte"
tem, no campo forte de hoje, uma única resposta não trivial.

Isso responde à condição de reabrir roteamento aprendido: **não há 2–3 regimes independentes
para aprender a rotear.** Há um, e ele já está implementado.

## O achado que muda o alvo: a chave está errada

Win rate dos assentos fortes por contexto e regime:

| contexto | regime | n | win rate |
|---|---|---:|---:|
| sem YARN | cow_led | 455 | 0,556 |
| sem YARN | mixed_herd | 451 | 0,579 |
| sem YARN | **sheep_led** | **76** | **0,671** |
| com YARN | sheep_led | 232 | 0,591 |

A elite roda ovelha **sem** loja de lã cedo, 76 vezes, e ganha 0,671 — enquanto o nosso
forçado deu 0,185. A diferença não é execução:

**Nesses 76 assentos, a lã estava sã em 76 de 76** (mediana 223, zero colapsos). Na nossa
célula A, a lã estava colapsada em **37 de 54**.

A elite não é mais esperta com ovelhas. **Ela nunca compromete com ovelha quando a lã não tem
comprador.** A loja nas duas primeiras posições é só um proxy disso.

Separando a célula A pelo mercado de lã:

| subgrupo | n | ovelha forçada | `v006` original | ganhos | regredidos | net |
|---|---:|---:|---:|---:|---:|---:|
| lã sã no d15 (>5) | 17 | **0,471** | 0,294 | 6 | 3 | **+3** |
| lã colapsada (≤5) | 37 | 0,054 | 0,297 | 0 | 9 | **−9** |

A perda inteira da célula A vem dos 37 mundos de lã morta. **Nos 17 com lã sã, forçar ovelha
ganha.**

A regra atual como classificador do colapso: `bal_acc` 0,717, **sensibilidade 1,000**,
especificidade 0,433. Ela nunca põe ovelha num mundo de colapso — o que é bom — mas nega
ovelha a 17 mundos em que a lã se sustenta, e são exatamente esses 17 que valem +3.

Trocando a chave por "lã vendável" no mesmo cohort: **29/67 = 0,4328** contra 26/67 = 0,3881
do `v006`, **+3 mundos, +0,045**.

## E o problema: a informação não existe no turno da decisão

Preço mediano da lã por dia, separado pelo destino no dia 15:

| dia | 1 | 3 | 5 | **6** | 7 | 9 | 11 | 15 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| colapsa no d15 (n=37) | 206 | 212 | 215 | **217** | 190 | 187 | 151 | **1** |
| sã no d15 (n=30) | 206 | 212 | 215 | **217** | 190 | 187 | 199 | **225** |

As séries são **idênticas até o dia 9**. O roteamento acontece no dia 6, e nesse ponto o preço
da lã não carrega nenhuma informação sobre o próprio futuro: `bal_acc` 0,500 em todos os dias
até o 11.

**O `v006` compromete o regime cinco dias antes de a variável que decide o regime existir.**

## Consequência para a arquitetura

O 2x2 confirma a recomendação B do relatório anterior — os regimes são reais e dependem do
contexto — mas move o trabalho:

1. **Não é construir mais regimes.** O campo forte só tem um regime condicionado a loja, e nós
   já o temos.
2. **Não é roteamento aprendido nem RL.** A condição declarada para reabrir isso eram 2–3
   regimes independentemente fortes. Existe um.
3. **É a chave e o momento do compromisso.** A variável certa é a demanda por lã, não a
   identidade da loja, e ela só se revela por volta do dia 11 — depois do turno 144 em que o
   `v006` decide.

O próximo experimento é adiar o compromisso: manter a regra da `YARN_STORE` como prior no dia
6 e reavaliar quando a demanda por lã se revelar, medindo no mesmo cohort com
`ladder_cohort.py`. O teto medido dessa mudança neste cohort é **+3 mundos (0,388 → 0,433)**,
com a ressalva de que os 17 mundos foram identificados no próprio cohort e a regra precisa ser
revalidada fora dele.

Nada foi promovido a candidato, nenhum sweep foi rodado, nenhum RL foi treinado, e o agente de
submissão não foi tocado.
