# Kaggriculture — achados do motor oficial (Fase 0)

Fonte de verdade: `kaggle_environments/envs/kaggriculture/kaggriculture.py`
(kaggle-environments 1.32.7, sha256 `bc8a5487…653e`, batendo com `config.yaml`).
`kaggriculture.json`, `README.md` e `AGENTS.md` foram lidos como apoio; onde houve
conflito, o `.py` prevaleceu (há um caso, ver Armadilha A9).

Todas as afirmações abaixo marcadas com um nome de teste estão travadas contra o
motor real em `tests/test_rules_timing.py`, `tests/test_rules_farm.py` e
`tests/test_rules_market.py`. As 20 categorias exigidas estão indexadas como
`[C1]`…`[C20]`.

## Estado em 2026-09-09, ao trazer este documento para o `master`

Este documento é da Fase 0 e foi escrito contra um `agent/` que mudou desde então.
Os 64 testes de regra continuam passando contra o mesmo motor 1.32.7, então as
**regras** (`[C1]`–`[C20]`) seguem válidas. Três ressalvas foram verificadas antes do
resgate, e valem para quem for usar as seções de armadilha e oportunidade:

- **A5/O2 está fechado.** `agent/market.py:49` e `:91` já leem
  `state.config.get('shedCapacity', 100)`. A afirmação de que o agente "não modela
  `shedCapacity`" era verdadeira na Fase 0 e não é mais.
- **A divergência da janela de rega é menor do que parece.** O bônus do motor só
  existe para culturas não-ongoing (`kaggriculture.py:437`), e o ramo correspondente
  do planner já tem `not ongoing`. WHEAT e CARROT coincidem; TOMATO e STRAWBERRY
  nunca entram no ramo. Sobra **MELON, e um dia** (idade 5 contra 6). A correção da
  branch de origem está certa, e é pequena.
- **As seções 3 e 4 miram uma linhagem que não é a que compete.** `agent/planner.py`
  perde 60–0 para o `versions/v006` (60 jogos, zero falhas dos dois lados, dinheiro
  final mediano $43.137 contra $166.037), e o `v006` não importa nada de `agent/`:
  ele replica fita gravada nos passos 0–711 e só decide nos passos 712–718. Corrigir
  a economia do nosso planner, hoje, melhora um agente que não é o candidato. Estas
  oportunidades voltam a valer na medida em que a janela de decisão do chassi que
  compete for alargada.

---

## 1. Regra confirmada

### [C1] Uma partida dá 719 ações, não 720
`episodeSteps = 720` grava 720 estados; o framework chama o agente 719 vezes, com
`obs['step']` indo de 0 a 718. A última jogada é dia 29, hora 22.
`interpreter` dispara `DONE` em `step >= episodeSteps - 2`.
`state.turns_left = episodeSteps - 1 - step` já estava **CORRETO** (719 no primeiro
turno, 1 no último). `submission/preflight.py` (`len(env.steps) != 720`) também.
→ `test_season_is_719_agent_turns_and_turns_left_matches`

### [C2] Ordem de resolução dentro de um turno
Exatamente, em `interpreter`:

1. Ações de unidade do jogador 0 — farmer primeiro, depois cada hand na ordem da lista;
2. Ações de unidade do jogador 1 (mesma ordem interna);
3. `_process_market` — fila de mercado dos dois jogadores;
4. `_town_consume` — town center + shops, seguido de refresh de preços;
5. `_decay_plants` para cada fazenda;
6. `_end_of_day`, se `(step + 1) % turnsPerDay == 0`;
7. atualização de `day`/`hour` e sincronização das observações;
8. checagem de `DONE`.

Como cada jogador só muta a própria fazenda, o passo 1 antes do 2 é
indistinguível de simultaneidade — só o mercado é compartilhado, e lá a
resolução é lockstep (ver C13).
→ `test_market_orders_resolve_after_unit_actions`, `test_drop_then_sell_settles_within_one_turn`

### [C3] BUY/HIRE/BUY_LAND só produzem efeito no turno seguinte
Todos resolvem na fase de mercado, depois de todas as ações de unidade:
- `BUY_SEED` → semente disponível para `PLANT` só no próximo turno;
- `BUY_PRODUCT` / `BUY_ANIMAL` → item cai no shed, precisa de `PICKUP` depois;
- `HIRE` → o hand nasce imediatamente e aparece em `farm['hands']`, mas não pode
  agir no turno da contratação;
- `BUY_LAND` → quadrante desbloqueado, utilizável a partir do turno seguinte.

A exceção na direção contrária: `DROP` (fase de unidade) alimenta um `SELL`
(fase de mercado) **no mesmo turno**.
→ `test_hired_hand_cannot_act_on_its_hire_turn`, `test_bought_land_is_only_usable_from_the_next_turn`

### [C4] PLANT e WATER no mesmo turno, na mesma tile
Cada unidade executa **uma** ação por turno, então uma unidade sozinha não planta
e rega no mesmo turno. Duas unidades sim: as ações são aplicadas em sequência e
mutam o tile imediatamente, então o farmer planta e um hand posterior na lista
rega a mesma tile no mesmo turno. Isso importa porque a planta precisa ser regada
no próprio dia do plantio (C5). `WATER` repetido no mesmo dia é no-op.
→ `test_plant_and_water_land_on_the_same_tile_within_one_turn`, `test_water_is_once_per_day_and_planting_day_counts_as_unwatered`

### [C5] Morte de plantas e animais
- Planta: `_new_plant` nasce com `consecutive_unwatered = 1`. No fim do dia, regada
  → 0; não regada → +1; ao chegar em 2 vira `{"kind": "WEED"}`. Logo: **plantio não
  regado no mesmo dia morre naquela noite**, e uma planta estabelecida tolera
  exatamente um dia seco.
- Animal: `consecutive_unfed` nasce em 0, então sobrevive ao dia da colocação sem
  comer. Dois dias consecutivos sem `FEED` → o animal **foge** e o tile volta a ser
  a estrutura vazia (`COOP`/`PASTURE`), reaproveitável.
- Weeds nunca somem sozinhas (C18).
→ `test_plant_survives_one_dry_day_and_weeds_on_the_second`, `test_unwatered_fresh_planting_dies_the_same_night`, `test_animal_escapes_after_two_unfed_days_leaving_the_structure`

### [C6] Colheita e decay
- One-time (`WHEAT`, `CARROT`, `MELON`): nasce com `yield_units = 1`;
  `max_lifespan_step = (planted_day + max_yield_day + 1) * turnsPerDay`.
- Ongoing (`TOMATO`, `STRAWBERRY`): nasce com 0; `max_lifespan_step = -1` até a
  produção agendada de número `max_yield`, quando passa a `(dia + 1) * turnsPerDay`.
- A partir de `max_lifespan_step`, `_decay_plants` tira 1 unidade a cada 2 steps;
  ao zerar, o tile vira `WEED`.
- `HARVEST` exige `day - planted_day >= first_yield_day` e `yield_units > 0`.
  One-time some do tile ao ser colhido; ongoing permanece com `yield_units = 0`.
- Animais **não** decaem: `yield_units` satura em `max_held` e a produção
  excedente é simplesmente perdida.

A tabela `agent/economy.py:CROPS` (`seed, first, peak, quantity, occupancy`)
reproduz o motor exatamente, incluindo `occupancy == peak + 1`:

| crop | first_yield_day | dia de pico | yield no pico (só água) | decay a partir de |
|---|---|---|---|---|
| WHEAT | 2 | 4 | 4 | dia 5 |
| CARROT | 2 | 3 | 3 | dia 4 |
| MELON | 10 | 10 | 6 | dia 13 |
| TOMATO | 8 | 11 | 4 (acumulado) | dia 12 |
| STRAWBERRY | 10 | 16 | 4 (acumulado) | dia 17 |

→ `test_crop_table_matches_engine_yield_curve`, `test_mature_one_time_crop_decays_every_other_step_then_weeds`, `test_ongoing_crop_decay_starts_after_its_last_scheduled_production`, `test_harvest_clears_one_time_crops_and_keeps_ongoing_ones`

### [C7] Semântica exata de CARE
`CARE` só marca `cared_today = True` (uma vez por dia). O efeito é no
`_daily_refresh_animals`, nesta ordem:

1. se é dia de produção: `bonus = pending_care_bonus` **se alimentado hoje**,
   senão 0; `yield_units += 1 + bonus`; `pending_care_bonus = 0` nos dois casos;
2. depois, se `cared_today and fed_today`: `pending_care_bonus += 1`.

Ou seja, CARE **banca** unidades, uma por dia alimentado+cuidado, e o banco
inteiro é pago na próxima produção agendada em que o animal estiver alimentado.
Para animais de intervalo longo isso multiplica muito a produção.
→ `test_care_banks_a_bonus_paid_on_the_next_fed_production`, `test_care_bonus_is_dropped_when_the_production_day_is_unfed`

### [C8] Fertilizante
- Origem 1: `COLLECT_FERTILIZER` em um tile com animal. `fertilizer_available` é
  uma **flag booleana** setada no fim de cada dia para todo animal vivo (inclusive
  o dia da colocação, ficando coletável a partir do dia seguinte). Não acumula.
- Origem 2: `BUY_PRODUCT FERTILIZER n` (junto com `WHEAT`, os dois únicos itens
  compráveis).
- `FERTILIZE` consome 1 do inventário da unidade e faz
  `fertilized_until_day = max(atual, day + 2)`, cobrindo `day`, `day+1`, `day+2`.
- Efeito: `+2` em vez de `+1` por rega dentro da janela (one-time) e por produção
  agendada regada (ongoing). O `min(max_yield, …)` continua valendo.
- O item em si não expira; só o buff.
→ `test_fertilizer_is_one_per_animal_per_day_and_does_not_stack`, `test_fertilize_doubles_the_water_bonus_for_three_days`, `test_fertilizer_window_covers_exactly_three_ongoing_productions`

### [C9] Shed tem capacidade — `shedCapacity` (default 100)
Conta itens não-semente; **animais comprados também ocupam vaga** (o shed tem
chaves de `PRODUCTS` + `ANIMALS`). Sementes vivem em `private['seeds']`, fora do
teto. Comportamento em overflow, por caminho:

| caminho | em overflow |
|---|---|
| `DROP` | enche até o teto e **descarta o resto** (o inventário é zerado) |
| `PLACE <item> n` (shed) | enche até o teto e **mantém a sobra no inventário** |
| drop automático de fim de dia | enche até o teto e **descarta o resto** |
| `BUY_PRODUCT` / `BUY_ANIMAL` | **recusa a unidade** e aborta o restante da ordem |
| `PICKUP` | sem restrição (esvazia o shed) |

→ `test_shed_capacity_bounds_every_deposit_path`, `test_market_purchases_are_refused_once_the_shed_is_full`

### [C10] Fim de dia
Roda apenas quando `(step + 1) % turnsPerDay == 0`. Por fazenda:
`_daily_refresh_plants` → `_daily_refresh_animals` → `_spawn_weeds` →
`_drop_inventories_to_shed` → farmer volta ao spawn padrão, `hands = []`,
`hires_today = 0`, `inventories = [{}]`. Depois, possivelmente, desbloqueio de
uma shop.

Todos os itens de todos os inventários vão para o shed automaticamente, com
descarte do que não couber. **Os hands somem toda noite** e precisam ser
recontratados.
→ `test_end_of_day_drops_inventories_and_resets_units`

### [C11] Ordem de processamento das ordens de mercado
A fila de cada jogador é truncada em `maxMarketOrdersPerTurn` (10). O motor então
percorre os índices `i = 0, 1, 2, …` e, para cada índice:

1. `HIRE` e `BUY_LAND` (atômicas) resolvem primeiro, na ordem dos jogadores;
2. as demais entram num laço unitário lockstep (C13);
3. `_refresh_prices` ao fim do índice.

Portanto **a ordem da lista importa**: um `SELL` no índice 0 levanta caixa antes
de um `BUY_*` no índice 1. Se uma unidade falha (sem dinheiro, sem estoque, shed
cheio, sub-op inválida), aquela ordem é abortada, mas os índices seguintes
continuam normalmente.
→ `test_market_orders_resolve_in_list_order`, `test_a_failed_unit_aborts_its_order_but_not_the_queue`, `test_orders_past_the_per_turn_limit_are_dropped`

### [C12] Timing do consumo da cidade
- Town center: quando `step % townCenterSellInterval == 0` (default 24), consome
  1 de cada produto **exceto FERTILIZER**. Com `turnsPerDay = 24`, uma vez por dia,
  taxa plana a temporada inteira.
- Shops: quando `step % townShopSellInterval == 0` (default 4), cada **instância**
  desbloqueada consome 1 de cada produto que demanda (2x se a shop tem um único
  produto). Seis ticks por dia.
- Desbloqueio: no fim do dia, se `(day + 1) % townShopUnlockInterval == 0`
  (default 3), sorteia uma shop **com reposição**, até 8 instâncias. A primeira
  aparece no step 72 (dia 3, hora 0).

`agent/economy.py:daily_demand` reproduz o dreno diário exatamente, incluindo
FERTILIZER = 0. **CORRETO.**
→ `test_town_center_and_shops_consume_on_fixed_step_intervals`, `test_daily_demand_matches_measured_town_drain`

### [C13] Ações de mercado simultâneas — preço não é travado
Dentro de um índice de ordem, o motor cota **a mesma unidade** para os dois
jogadores a partir do mesmo estoque pré-commit, e só depois comita os dois. Logo:
- não existe vantagem de "primeiro a agir" dentro do turno;
- o preço **se move dentro do turno**, unidade a unidade — não é travado no início;
- vender 5 morangos simultâneos rendeu exatamente o mesmo para os dois jogadores,
  e diferente do que uma resolução sequencial daria.
→ `test_both_players_are_quoted_the_same_price_for_each_unit`

### [C14] Fórmula de preço — `agent/economy.py:price` está correta
```
price(inv) = base + sign * amp * f(|inv - I0|)     sign = +1 se inv < I0, senão -1
amp        = target * base / f(T)
retorno    = max(1, round(price))
```
com `f ∈ {linear, sq, sqrt, log(ln(1+x)), log10, hinge}` e
`hinge(x) = u + 8*max(0, u-1)^2`, `u = x/T`.

Comparação exaustiva de `price()` contra `market_price()` para os 9 produtos em
todo o intervalo 0..20000: **zero divergências**, inclusive com overrides
esparsos de `marketParams`. `sale_value()` também reproduz a sequência de venda
real, incluindo a regra "venda a $1 não aumenta o estoque do mercado".
Diferença cosmética irrelevante: o agente usa `math.log1p(x)` e o motor
`math.log(1.0 + x)` — nenhum caso testado divergiu após o arredondamento.
→ `test_price_model_matches_official_across_the_whole_curve`, `test_price_model_honours_market_param_overrides`, `test_sale_value_matches_a_real_sell_order`, `test_sales_at_the_price_floor_do_not_add_market_supply`

### [C15] Farm hands
- Custo do n-ésimo hire **do dia**: `farmHandCostMult * fib(n)`, com
  `fib = 1, 1, 2, 3, 5, 8, 13, 21, …`. O par rolante `a, b = 1, 1; …; a, b = b, a+b`
  em `agent/planner.py` percorre exatamente essa sequência. **CORRETO.**
- `hires_today` zera todo fim de dia, junto com o sumiço dos hands.
- Spawn: um dos quatro tiles de acesso ao shed `[(4,4), (5,4), (4,5), (5,5)]`,
  escolhido por menor ocupação com desempate na ordem NWSE. Com o farmer em
  (4,4), o primeiro hire do dia nasce em **(5,4)**, que é LOCKED enquanto o NE não
  for comprado — mas tiles bloqueados são atravessáveis.
- Não há teto de hands no motor; o teto real é `maxMarketOrdersPerTurn`, porque
  cada `HIRE` é uma ordem.
→ `test_hire_cost_is_fibonacci_and_resets_each_day`, `test_hands_spawn_on_shed_access_tiles_in_nwse_order`

### [C16] Tiles bloqueados
Só o quadrante NW começa livre. Movimento sobre LOCKED é permitido de propósito
(um hand pode nascer num tile bloqueado). Ações de tile (`PLANT`, `WATER`, `DIG`,
`BUILD_*`, `HARVEST`, …) são no-op em LOCKED. As ações de shed (`PICKUP`, `DROP`,
`PLACE`-no-shed) resolvem **antes** da checagem de LOCKED e funcionam de qualquer
um dos quatro tiles centrais, inclusive bloqueados.
→ `test_locked_tiles_are_passable_but_reject_tile_actions`, `test_shed_actions_work_from_a_locked_access_tile`

### [C17] Ordem e preço de BUY_LAND
`LAND_ORDER = ["NE", "SW", "SE"]`, `LAND_PRICES = [1000, 2000, 4000]`, indexados
por `len(unlocked_quadrants) - 1`. Máximo de 3 compras; a quarta é no-op. Só tiles
`"LOCKED"` do quadrante viram `None`. O literal `(1000, 2000, 4000)[quadrants - 1]`
em `agent/planner.py:182` está **CORRETO**, e a ordem de expansão é NW → NE → SW → SE.
→ `test_land_prices_and_order_match_the_planner_literals`, `test_buy_land_unlocks_quadrants_in_ne_sw_se_order`

### [C18] Weeds
Três origens: (a) sorteio no fim do dia, por tile `None` desbloqueado, com
probabilidade `weedSpawnChance` (0.005), RNG `random.Random((seed*1_000_003) ^ day)`
compartilhada entre os dois jogadores e o sorteio de shops; (b) planta com dois
dias sem água; (c) planta madura cujo `yield_units` chegou a 0 no decay.
São **permanentes** até um `DIG`. Depois do `DIG` o tile volta a `None` e volta a
ser elegível a weed. `DIG` remove planta, weed e coop/pasto vazio, mas **não**
remove um animal colocado, e é no-op em tile vazio.
→ `test_weeds_persist_until_dug_and_can_respawn`, `test_weeds_only_spawn_on_empty_unlocked_tiles`, `test_dig_removes_plants_weeds_and_empty_structures_but_not_animals`, `test_weed_spawn_is_seeded_and_reproducible`

### [C19] Endgame e reward
`s.reward = float(farm["money"])`. **Não há liquidação automática de nada.** O que
estiver no shed ou em inventário de unidade no fim vale zero. Empate é possível.
Os comentários em `agent/planner.py:39-40` e `:85-86` ("entrega explícita antes do
fim; o drop automático da última noite é tarde demais") estão **CORRETOS** — e o
motivo é ainda mais forte do que o comentário diz: o drop automático da última
noite **nem chega a acontecer** (ver Armadilha A9).
→ `test_reward_is_final_money_and_ignores_unsold_stock`, `test_final_day_never_runs_an_end_of_day_refresh`

### [C20] Ações inválidas são no-ops silenciosos
`_apply_unit_action` é literalmente documentada assim no motor, e o código
confirma: toda guarda faz `return`. Nada de exceção, nada de custo, nada de
penalidade. Vale para: colher tile vazio, colher planta imatura, plantar sem
semente, plantar crop inexistente, mover para fora do tabuleiro, agir em tile
LOCKED, `DROP`/`PICKUP` longe do shed, argumentos faltando, op desconhecida, e
payloads malformados (string, `None`, inteiro, lista vazia). Ações endereçadas a
hands que não existem também são ignoradas. Ordens de mercado malformadas são
descartadas por `_parse_order`; sub-ops inválidas (`BUY_PRODUCT MELON`,
`SELL GOOSE`, `BUY_SEED BANANA`) anulam a ordem em silêncio.

Única saída visível do motor: um `print` de WARNING em `HARVEST` de crop ongoing
imaturo — segundo o próprio comentário, um caso que "nunca deveria acontecer".
→ `test_illegal_unit_actions_are_silent_no_ops`, `test_malformed_market_orders_never_raise`, `test_malformed_top_level_actions_do_not_crash_the_interpreter`, `test_actions_for_units_that_do_not_exist_are_ignored`, `test_buy_product_is_restricted_and_animals_cannot_be_sold`

---

## 2. Armadilha

**A1 — Validação atômica de PLANT.** Se o total de pedidos `PLANT <crop>` num
turno exceder as sementes disponíveis daquele crop, o motor converte **todos** eles
em `PASS` — não planta nenhum, nem parcialmente. Um agente que despacha hands em
paralelo sem reservar sementes localmente perde o turno inteiro. `agent/planner.py`
já faz a reserva (`seeds[action[1]] -= 1`), corretamente.

**A2 — Mercado depois das unidades.** `BUY_SEED` + `PLANT` no mesmo turno não
funciona; `HIRE` não dá ação ao hand naquele turno. Qualquer plano que assuma
"comprei, logo posso usar" perde um turno por transação.

**A3 — Reset noturno total.** Hands somem, o farmer teleporta para (4,4),
inventários vão para o shed e `hires_today` zera. Qualquer plano posicional
multi-dia é inválido, e o custo de deslocamento reinicia todo dia a partir do
centro.

**A4 — O primeiro hand do dia nasce em (5,4), que é LOCKED** até o NE ser
comprado. Ele é atravessável, mas custa pelo menos um turno para voltar à terra
útil.

**A5 — Overflow do shed é descartado em silêncio** no `DROP` e no drop de fim de
dia (`PLACE` é o único caminho que preserva a sobra). Pior: `BUY_PRODUCT` e
`BUY_ANIMAL` **falham** quando o shed está cheio, e a falha aborta o resto da
ordem. `agent/planner.py` **não modela `shedCapacity`** — é uma lacuna real, hoje
mascarada por `sell_fraction = 1.0` esvaziar o shed quase todo turno.

**A6 — Animais produzem mesmo sem comer.** `_daily_refresh_animals` produz a base
1 desde que o animal esteja vivo; alimentar serve para (a) não fugir em 2 dias e
(b) liberar o bônus de CARE. Um agente que trata `FEED` como pré-requisito de
produção gasta trigo demais; um que ignora `FEED` perde o animal em 2 dias.

**A7 — O banco de CARE é perdido inteiro** se o animal não estiver alimentado no
dia da produção agendada (produz 1 e zera o banco). CARE sem FEED consistente é
desperdício.

**A8 — Fertilizante não acumula por animal.** É uma flag; deixar de coletar por 5
dias ainda dá 1 unidade. Coleta atrasada = produção perdida.

**A9 — O último dia não tem fim de dia, e a doc oficial está errada sobre isso.**
`_end_of_day` só roda em `(step+1) % 24 == 0`; o último step jogado é o 718, então
o último fim de dia foi no step 695. O dia 29 tem apenas 23 turnos e **nenhum**
drop automático. `README.md` do motor diz "30 dias / 720 turnos"; o motor entrega
719 ações. Código > doc.

**A10 — Piso de $1 assimétrico.** Unidades vendidas com cotação $1 não entram no
estoque do mercado, então o preço fica preso no piso e não afunda mais — mas o
vendedor continua recebendo $1 por unidade. Despejar excedente premium é quase
gratuito em termos de dano ao preço, e quase inútil em termos de receita.

**A11 — Animais no shed não são vendáveis.** `SELL` só aceita itens em `PRODUCTS`;
`GOOSE`/`COW`/`SHEEP` comprados e não colocados ficam presos no shed ocupando
capacidade até o fim da partida, valendo zero no reward.

**A12 — `obs['market']['prices']` é um preço velho.** Ele reflete o estado após o
`_town_consume` do turno **anterior**, e dentro do turno o preço se move a cada
unidade vendida (pelos dois jogadores). Para decidir volume, use
`obs['market']['inventory']` mais a fórmula, não `prices`.

**A13 — Ordens acima de `maxMarketOrdersPerTurn` somem sem aviso.** O planner
emite os `SELL` antes de `HIRE`/`BUY_*`; num turno com muitos tipos de item no
shed (até 9 `SELL`), as compras e contratações podem ser cortadas.

**A14 — Planta madura não colhida vira weed e bloqueia o tile.** O decay começa 1
dia depois do `max_yield_day` e come 1 unidade a cada 2 steps; quando zera, o tile
precisa de um `DIG` antes de ser replantado.

---

## 3. Oportunidade de otimização
*(dentro do escopo simples/enxuto — NÃO implementado nesta fase)*

**O1 — `DROP` e `SELL` no mesmo turno. FECHADO.** `agent/market.py:88`
(`projected_shed`) espelha a ordem PICKUP/DROP, então o estoque entregue neste turno já
entra na lista de `SELL`. Era verdade na Fase 0.

**O1 (texto original)** — O motor permite (unidades antes do
mercado), mas `agent/planner.py` monta as ordens a partir de
`s.private['shed']`, ou seja, só do que já estava no shed no início do turno.
Cada entrega perde um turno de caixa; no último turno da temporada, é a diferença
entre vender e não vender. Bastaria antecipar os `DROP` planejados ao montar a
lista de `SELL`.

**O2 — Modelar `shedCapacity`. FECHADO.** `agent/market.py:49` e `:91` leem
`state.config.get('shedCapacity', 100)`; a pressão de estoque e o teto por entrega já
existem. A afirmação de que "não existe nenhum uso de `shedCapacity` no agente" era
verdadeira na Fase 0.

**O3 — CARE em animais de intervalo longo. FECHADO.** `agent/economy.py:care_priority`
passou a precificar o CARE pela unidade que ele efetivamente deposita — `45 +
prices[produto] * .3`, na mesma forma do FERTILIZE — e a devolver `None` quando o
`max_held` não deixa espaço para o bônus ou quando não sobra dia de produção na
temporada. Travado contra o motor em `tests/test_planner_care_and_water.py` e ablável
por `care_pricing`. Texto original abaixo.

**O3 (texto original)** — `SHEEP` (interval 3) com CARE diário
rendeu **6 unidades** na primeira produção contra **1** sem CARE (medido em
`test_care_banks_a_bonus_paid_on_the_next_fed_production`). `COW` (interval 2) tem
efeito parecido. Hoje o planner dá prioridade 45 a `CARE`, abaixo de quase tudo,
o que faz sentido para `GOOSE` (interval 1, ganho no máximo 2x) mas subestima
muito os outros dois.

**O4 — Regar no último dia ainda vale. FECHADO.** A guarda
`s.turns_left > s.turns_per_day - s.hour` cortava o último dia inteiro; agora a rega
continua enquanto a planta estiver na janela de bônus e restar um turno para colher o que
a água acabou de somar. Ablável por `last_day_water`. Texto original abaixo.

**O4 (texto original)** — `WATER` dentro da janela incrementa
`yield_units` na hora, então regar no penúltimo turno e colher no último soma
unidades reais. `agent/planner.py:51` desliga a rega quando
`turns_left <= turns_per_day - hour`, o que corta esse ganho.

**O5 — Contratar é praticamente de graça. EM ABERTO, DE PROPÓSITO.** `max_hands` é um
parâmetro de tuning, não um bug: mudá-lo sem medição pareada é exatamente o tipo de
chute que a varredura de capacidade do Ray desmentiu. Fica para um experimento com
`max_hands` no eixo. Texto original abaixo.

**O5 (texto original)** — 8 hands custam $54 por dia, 12 custam
$376. `max_hands = 8` é um limite do agente, não do motor. O gargalo verdadeiro é
`maxMarketOrdersPerTurn = 10` (cada `HIRE` é uma ordem) e a competição por espaço
na fila com os `SELL`.

**O6 — Folga de colheita do melão.** Yield 6 é atingido no dia 10 e o decay só
começa no dia 13: três dias inteiros para agendar a colheita sem perda, útil para
suavizar picos de deslocamento.

**O7 — FERTILIZER não tem demanda de cidade.** Nenhuma shop e nem o town center
consomem fertilizante, então seu estoque só sobe (vendas) e desce (compras).
Isso torna o preço de compra previsível e o de venda estruturalmente
deteriorante — vale comprar cedo e nunca contar com receita de venda.

---

## 4. Possível vantagem competitiva futura
*(roadmap "DEPOIS" — NÃO implementado, e explicitamente fora da V0.1)*

**V1 — Guerra de preço / envenenamento de mercado.** `above_target > 1` em
morango, melão, leite e lã leva ao piso $1 com pouquíssimas unidades: **62
morangos** zeraram a cotação num teste. Como o mercado é compartilhado e vendas no
piso não somam estoque, o dano fica travado no nível do piso e não se recupera
sozinho. Dá para derrubar deliberadamente o mercado que o oponente está
construindo antes da colheita dele — a informação necessária (tiles do oponente)
é pública.

**V2 — Segurar oferta em curvas `hinge`.** `CARROT`, `TOMATO` e `EGG` usam `hinge`
no lado da escassez: o preço fica calmo até `I0 - T` e depois dispara. Como a
cidade drena esses itens sozinha e ninguém pode comprá-los de volta, não vender
cenoura por vários dias empurra o preço muito acima da base. Segurar estoque pode
valer mais do que vender cedo — mas exige modelar `shedCapacity` (O2).

**V3 — Leitura do oponente.** `obs['farms'][1 - player]` expõe tiles, dinheiro,
quadrantes desbloqueados e hands do adversário. Dá para contar as plantas dele,
estimar `first_yield_day + max_yield_day` de cada uma e prever a janela em que ele
vai despejar oferta — e vender antes.

**V4 — Adaptação às shops sorteadas.** `unlocked_shops` é sorteado **com
reposição**, então uma temporada pode ter três padarias e nenhuma loja de lã. A
demanda realizada por item é observável e cresce monotonicamente; escolher a
cultura pela demanda observada, e não pela tabela estática, é uma vantagem
estrutural que o `adaptive` atual só explora parcialmente.

**V5 — Ovelha + CARE como multiplicador.** Combinação de O3 com `WOOL`
(base $200, `above_func = sq`): poucas unidades derrubam o preço, então lã só vale
em volume pequeno e bem cronometrado — exatamente o perfil que CARE produz
(rajadas grandes em intervalos de 3 dias).

**V6 — Fertilizante comprado em melão.** $100 de fertilizante compra +1 unidade
por dia regado durante 3 dias. Em melão (base $250) o retorno é grande, e o
planner atual só usa o fertilizante coletado dos animais — com `animal_target = 0`
no default, ele nunca fertiliza nada.

---

## 5. Não confirmado com certeza

- **Ordem relativa entre `_town_consume` e `_decay_plants`.** Lida diretamente no
  código (`_process_market` → `_town_consume` → `_decay_plants` → `_end_of_day`),
  mas não é observável externamente, porque as duas rotinas não compartilham
  estado. Travada apenas por leitura, não por teste.
- **Configurações fora do default.** Tudo aqui foi verificado com `boardSize = 10`,
  `turnsPerDay = 24`, `shedCapacity = 100`, `farmHandCostMult = 1`, 2 agentes —
  os valores da competição. O motor aceita outros, e as fórmulas usam as chaves de
  configuração corretamente, mas não há teste para eles. Nota: `agent/economy.py:shape`
  divide por `T` no ramo `hinge` sem a proteção `if not T or T <= 0` que o motor
  tem, então um override `marketParams` com `T = 0` levantaria `ZeroDivisionError`
  no agente e não no motor. Cenário irreal na competição.
- **Penalidade oficial por exceção/timeout do agente.** O interpretador não
  contém nada sobre isso; é política do runner da Kaggle. Localmente,
  `arena/match.py` substitui a ação por `PASS` e registra a falha, o que é uma
  escolha do laboratório, não uma regra confirmada do motor.
- **`agent/planner.py:182`** indexa `(1000, 2000, 4000)[quadrants - 1]` sem
  checar o limite de 3 quadrantes compráveis. Com `max_quadrants <= 4` (todos os
  valores em uso) o índice nunca estoura, então não foi alterado; com
  `max_quadrants > 4` seria `IndexError`, ou seja, derrota imediata. Vale lembrar
  se algum sweep futuro mexer nesse parâmetro.
