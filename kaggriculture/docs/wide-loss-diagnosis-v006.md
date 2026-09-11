# Diagnóstico das derrotas largas do `v006` na faixa 2600–2800

Instrumentos: `experiments/wide_loss_timeline.py` (extração), `experiments/wide_loss_diagnosis.py`
(comparação). Artefatos: `artifacts/wide_loss_comparison_v006.json`,
`artifacts/wide_loss_comparison_v006.csv`, `artifacts/wide-loss-timelines-v006.json`,
`artifacts/wide-loss-terminal-v006.json`, `artifacts/wide-loss-shops-v006.json`.
Populações: **A** = 23 derrotas largas, **B** = 40 apertados, **W** = 4 vitórias largas,
mantido separado e nunca somado a B.

Nenhum parsing foi reimplementado: `elite_events.summarize_state` fornece o estado observável
e `elite_events.canonical_turn` já trata o `step` ausente no seat 1.

Uma regra governa todos os fluxos: **ordens de mercado são pedidos**, e um pedido recusado é
indistinguível de um atendido no stream de ações. Compras, vendas e produção aqui são lidas
como **transições de estado**. Só o `PASS` é lido do stream, porque não pode ser recusado.

---

## Resumo executivo

A pergunta como foi formulada — a causa comum das 23 derrotas largas — **não tem resposta,
e o motivo é informativo**. Os 23 não são uma população natural, e o `v006` não faz nada
de diferente neles.

O que existe é outra coisa, mais acionável: **o `v006` tem dois regimes econômicos, e o
gatilho entre eles é uma loteria do mapa.** O regime bom ganha 0,769; o ruim ganha 0,296;
e o ruim é escolhido em 54 dos 67 mundos.

---

## 1. Mundo / ambiente

Engine `1.32.7` e `statuses` `['DONE','DONE']` em 67 de 67. Seeds distintas por episódio.
Seats equilibrados (34 no seat 1, 33 no seat 0). Preços em t0 idênticos em mediana entre os
grupos (WOOL 206, MILK 169, EGG 50, MELON 256) — o `environment_check` no JSON traz produto a
produto e nenhum separa A de B.

O inventário do town começa saturado (9.999 por produto), então inventário de mercado não
carrega informação nos primeiros dias.

## 2. Abertura

**Não há divergência de abertura, porque não há abertura variável.** Contagem de valores
distintos entre os 67 mundos, por dia:

| dia | land | crops | herd | hands | pasture | coop | shops | cash | opp_cash |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 6 | 6 |
| 5 | 1 | 1 | 2 | 1 | 2 | 1 | 1 | 8 | 12 |
| 10 | 1 | 3 | 2 | 1 | 2 | 1 | 1 | 50 | 52 |
| 20 | 1 | 3 | 3 | 1 | 3 | 2 | 1 | 67 | 67 |
| 29 | 1 | 1 | 3 | 1 | 3 | 2 | 1 | 67 | 67 |

`land_owned` e `hands` têm **um único valor em todos os dias, em todos os 67 mundos**. O
`v006` compra a mesma terra na mesma hora com a mesma equipe contra qualquer adversário e
qualquer seed. Só o `cash` varia, e cash é consequência.

Isso torna a análise de primeira divergência *das nossas decisões* vazia por construção, e é
por isso que o detector precisou de um piso de materialidade: o envelope do coorte apertado é
frequentemente degenerado (um único valor), e sem o piso uma moeda de ruído virava
"divergência".

## 3. Economia no tempo

Todas as séries diárias estão em `artifacts/wide-loss-timelines-v006.json` (cash, delta cash,
terra, animais, crops, estoque, fluxos por produto, preços, `passes`, `idle_hands`). O CSV
traz a comparação A×B em 6 checkpoints para ~30 features.

Nenhum efeito é forte. O maior |Cliff's delta| do conjunto inteiro é **−0,436**
(`cash_lead`, dia 20) e é jusante — mede que já estamos perdendo. Abaixo de dia 10 nada passa
de |0,24|, e as medianas de A e B são frequentemente **idênticas** (cash d1 26 vs 26; d3 198
vs 198; d5 686 vs 686).

## 4. Exposição de mercado

O preço da lã colapsa (≤5) no dia 15 em **16 de 23 derrotas e 21 de 40 apertados** — 55% de
todos os mundos. `P(derrota larga | colapso) = 0,432` contra base 0,343.

Ou seja: **o glut existe e atinge quase todo mundo, mas não discrimina.** Vender lã em glut é
um problema global do agente, não a causa das derrotas largas.

## 5. Endgame

**Limpo, e isso corrigiu uma leitura minha errada.** Amostrando o dia 29 à hora 0 aparecem 98
unidades em estoque, o que sugeria ~4.500 moedas paradas. Está errado: o agente ainda vende
uma mediana de 8.780 moedas nas últimas 24 horas.

O estado terminal real no turno 719 (`artifacts/wide-loss-terminal-v006.json`):

- estoque terminal = **0 unidades em 67 de 67 mundos**;
- `cash` terminal == `reward` em **67 de 67**.

Não há dinheiro não monetizado no sino, e não há hipótese de higiene de liquidação.

## 6. Primeira divergência — e o que ela virou

Com o piso de materialidade, as 23 derrotas divergem do envelope apertado em dia mediano 8
(6 no dia 1, 3 no dia 7, 4 no dia 8, 3 no dia 12, 1 no 13, 4 no 14, 2 nunca). Os sinais são
dominados por `cash_lead` (16), `opponent_cash` (7) e `cash` (5) — todos jusante.

**A divergência que importa não é entre A e B. É entre os dois regimes do próprio agente.**

Rastreando ovelhas, pasto e coop por dia, os dois ramos são idênticos até o dia 6 e separam
no **dia 7**:

| dia | ovelhas P | ovelhas C | pasto P | pasto C | coop P | coop C | cash P | cash C |
|---:|---|---|---|---|---|---|---:|---:|
| 1–6 | 2 | 2 | 4,6 | 4,6 | 0 | 0 | iguais | iguais |
| 7 | 2,4 | 2 | 13 | 11,13 | 0 | 0 | 801 | 750 |
| 11 | 9,10,11 | 3,6 | 17,18 | 12,14 | 0,1 | 4 | 14.722 | 14.638 |
| 15 | 10,11,12 | 3,6 | 17,18 | 12,14 | 0,1 | 4 | 22.264 | 23.342 |

O cash é **indistinguível** quando a bifurcação ocorre. Não é consequência de estar à frente.

### O gatilho é a `YARN_STORE`

Cruzando a identidade das lojas destravadas com o ramo:

| dia | concordância entre "YARN_STORE destravada" e o ramo |
|---:|---:|
| 3 | 58/67 = 0,866 |
| **7** | **67/67 = 1,000** |
| 9 | 58/67 = 0,866 |

**Se a `YARN_STORE` está entre as duas primeiras lojas, o agente entra no regime
ovelha/pasto. Se não está, entra no regime coop.** Sem exceção nos 67 mundos.

Isso é consistente com o que o `v006` é: a descrição da própria submissão diz "9 of 15 yarn
shop routes refitted locally". O roteador está funcionando como projetado — ele roteia para a
rota de lã quando a loja de lã aparece cedo.

### O que os dois regimes valem

| regime | n | vitórias | win rate | margem mediana |
|---|---:|---:|---:|---:|
| YARN cedo (ovelha/pasto) | 13 | 10 | **0,769** | +18 |
| sem YARN cedo (coop) | 54 | 16 | **0,296** | −691 |

Diferença **+0,473**, bootstrap CI95 **[+0,205, +0,704]** — exclui zero.

**Temos uma economia que ganha 0,769 e só conseguimos rodá-la em 19% dos mundos, porque o
gatilho é uma loteria do mapa.**

---

## Estatística

Nada aqui foi selecionado por p-valor. Cada feature no CSV traz mediana e média dos dois
grupos, Cliff's delta, overlap, CI bootstrap da diferença de medianas, p de permutação
(contexto apenas) e quantos dos 23 ela toca.

### Opponent check

| medida | valor |
|---|---|
| times distintos nas 23 derrotas | 22 |
| times distintos nos 40 apertados | 38 |
| times presentes nos dois grupos | **1** |
| maior fatia de um time nas derrotas | 2/23 = 0,087 |
| rating do oponente, mediana A / B | 2.654 / 2.626 |
| CI95 da diferença de medianas | **[−5,8, +54,8]** — contém zero |

**Identidade e força do adversário não explicam o split.** E o regime também não é do
adversário: nos dois ramos o rating mediano é 2.636 contra 2.635, e os seats são equilibrados.

### Classifier diagnostic

Stump raso, limiar no ponto médio, validação leave-one-world-out (cada mundo tem sua própria
seed, então isso é a validação por seed correta). **E o procedimento inteiro foi testado
contra permutação**, porque validar uma feature não valida a *escolha* entre 132 candidatas:

| dia | melhor feature | LOO bal. acc. |
|---:|---|---:|
| 1 | `cash_lead` | 0,543 |
| 3 | `cash_lead` | 0,532 |
| 5 | `price_EGG` | 0,592 |
| 10 | `price_EGG` | 0,700 |

**Nulo de seleção** (mesmo pipeline, rótulos embaralhados, 200 rodadas): mediana **0,630**,
p95 **0,681**, máx **0,709**.

Dias 1, 3 e 5 ficam **abaixo da mediana do nulo**. O 0,700 do dia 10 está dentro da cauda do
ruído de seleção. **Derrota larga versus não é imprevisível cedo.**

Uma limitação da minha primeira passagem, encontrada e corrigida: o classificador guardava
`known_shops` como *contagem*, não identidade, e por isso não podia enxergar a `YARN_STORE`.
A contagem é idêntica nos dois ramos (0,0,1,1,2,3,5); só a identidade separa.

E a `YARN_STORE` **não** é um bom classificador de derrota larga — `bal_acc` 0,615,
sensibilidade 0,957, especificidade 0,273. Ela é condição suficiente para o regime bom, não
necessária para a derrota. Isso é coerente: o alvo certo nunca foi "derrota larga", foi
vitória.

---

## A aritmética que reenquadra tudo

A distribuição das 67 margens é contínua e não tem vale em 1.000:

    [0,50) 16 | [50,100) 3 | [100,250) 10 | [250,500) 2 | [500,1k) 9
    [1k,2k) 12 | [2k,4k) 5 | [4k,8k) 8 | [8k,∞) 2

Mudando o corte, a composição muda suavemente (500 → 30 derrotas largas; 1.500 → 16). **O
corte de 1.000 foi imposto pela análise, não pelos dados.**

E os flips baratos não estão entre os 23:

| ganho uniforme | flips | vitórias | win rate |
|---:|---:|---:|---:|
| +100 | 8 | 34 | 0,508 |
| +200 | 9 | 35 | 0,522 |
| +350 | 10 | 36 | 0,537 |
| **+630** | **12** | **38** | **0,567** |
| +1.000 | 18 | 44 | 0,657 |

Três mundos foram perdidos por **2 moedas**; seis por 21 ou menos. Os 12 flips pedidos custam
**+629 moedas** e todos estão nas derrotas apertadas.

O ganho uniforme é uma idealização — nenhuma intervenção real soma o mesmo em todo mundo — e
por isso a curva é um **piso** sobre o que uma intervenção precisa entregar, não uma promessa.
Seu valor é estar denominada nas mesmas unidades em que o ladder decide.

---

## TOP HYPOTHESES

| # | sinal | 1º observável | afeta dos 23 | afeta dos 40 | effect size | mecanismo plausível | intervenção candidata | risco de regressão |
|---:|---|---|---:|---:|---|---|---|---|
| 1 | `YARN_STORE` ausente entre as 2 primeiras lojas → regime coop | **dia 7** (dia 3 em 4 mundos) | 22/23 | 32/40 | Δ win rate **+0,473**, CI [+0,205, +0,704] | O roteador só alcança a economia ovelha/pasto quando a loja de lã abre cedo; nos outros 54 mundos cai na linha coop, que ganha 0,296 | Tornar a economia ovelha/pasto alcançável sem `YARN_STORE` cedo, ou trocar a rota coop por outra | **Alto e não estimável por observação** — os 0,769 são medidos só onde a loja existia; ver falsificação abaixo |
| 2 | Margem decidida por <1.000 moedas | fim do jogo | 0/23 | 40/40 | 40/67 dos mundos | O objetivo local mede em ±81.000; o ladder decide em ±102 | Qualquer intervenção precisa ser avaliada em moedas no cohort, não em margem média | Baixo — é mudança de métrica |
| 3 | Colapso do preço da lã (≤5) até o dia 15 | dia 15 | 16/23 | 20/40 | lift 0,432 vs base 0,343 | Cronograma de venda fixo despeja lã em glut | Antecipar/escalonar a venda de lã | Médio — atinge 55% dos mundos, inclusive vitórias |
| 4 | `cash_lead` negativo no dia 20 | dia 20 | 16/23 | — | Cliff's δ **−0,436** | Jusante: mede que já perdemos | Nenhuma — diagnóstico, não causa | n/a |
| 5 | `price_EGG` > 52 no dia 10 | dia 10 | 1/23 | — | LOO 0,700, **dentro do nulo** (p95 0,681) | Possivelmente regime de mercado | Nenhuma — não sobrevive ao nulo de seleção | n/a |

Só a hipótese 1 satisfaz os cinco critérios pedidos (frequente, anterior à derrota, observável
em runtime, corresponde a uma decisão nossa) — e ela falha exatamente no quinto, o risco de
regressão, que não pode ser estimado a partir destes dados.

---

## Recomendação de arquitetura

**B — EARLY REGIME ROUTER**, com uma ressalva que muda o trabalho.

Contra **A (patch global)**: existem dois regimes com resultados muito diferentes
(0,769 contra 0,296), e o gatilho é conhecido e perfeitamente observável no dia 7. Uma
mudança que dominasse independentemente do regime ignoraria a única estrutura forte
encontrada nos dados.

Contra **C (adaptação em runtime)**: o regime **não** emerge durante a partida. Ele está
determinado no dia 7 pela identidade das duas primeiras lojas, que é do mapa. Não há estado
tardio a que reagir; adaptar em runtime resolveria um problema que não é este. (E o
repositório já rejeitou uma forma de seleção condicionada a estado, 0/50 em
`docs/STATE_OPTION_COUNTERFACTUAL.md`.)

A favor de **B**: o regime é detectável cedo, com acurácia 1,000 no dia 7, por uma feature
categórica trivial.

**A ressalva:** o roteador já existe e já acerta. O `v006` roteia para a rota de lã sempre que
a `YARN_STORE` abre cedo, e ganha 0,769 quando isso acontece. O trabalho não é construir um
roteador — é que **o regime bom só é alcançável em 19% dos mundos**, e a pergunta é se ele
pode ser alcançado sem a loja que hoje o destrava.

**Isto é uma hipótese, não um achado.** Os 0,769 são medidos exclusivamente em mundos onde a
`YARN_STORE` abriu cedo. É perfeitamente possível que a loja seja o que torna a lã vendável, e
que forçar o build ovelha/pasto sem ela produza a pior das duas economias. Esse é um problema
de seleção sobre o tratamento e **não pode ser resolvido com dados observacionais de replay**.

### O teste que decide

Rodar `experiments/ladder_cohort.py` com um candidato que force a economia ovelha/pasto nos
**54 mundos sem YARN cedo** e ler `gained` contra `regressed`. O cohort já é exatamente essa
população, o instrumento já reporta flips por episódio, e o custo é uma passada de 54 partidas.

Se `net_worlds` for positivo, a hipótese 1 vira intervenção. Se for negativo, a loja era o
mecanismo e a resposta certa passa a ser A sobre a rota coop — que é onde 54 dos 67 mundos
vivem, e onde 22 das 23 derrotas largas estão.

Nada foi implementado, nenhum sweep foi rodado, nenhum RL foi treinado e o agente de
submissão não foi tocado.
