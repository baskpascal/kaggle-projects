# O compromisso adiado é rejeitado: o custo de transição excede a informação

Instrumento: `experiments/delayed_commitment.py`. Evidência:
`artifacts/delayed_commitment_oracle_v006.json`. População: os 54 mundos do cohort sem
`YARN_STORE` entre as duas primeiras lojas.

## A hipótese

O `v006` compromete o regime no turno 144 (dia 6). A variável que decide se a economia ovelha
paga — se a lã ainda tem comprador — só separa por volta do dia 11. Preservar opcionalidade
até a informação chegar poderia superar o compromisso irreversível atual.

## A ponte

A `Policy` do `v006` lê `self.tapes[state.plan]` a cada turno e atribui `state.plan` uma vez,
no turno 144. A ponte atribui duas vezes: um plano de prefixo no 144 e um plano de sufixo no
turno 264 (dia 11, hora 0 — o primeiro turno em que a demanda por lã separa). Todos os
overlays posteriores ficam intactos.

O rótulo do oráculo lê o mercado de lã **futuro** (dia 15) e é usado apenas para compor a
escolha por mundo offline. Ele nunca entra num agente: um teste falha se as palavras `oracle`
ou `healthy` aparecerem no `router_parent.py` gerado.

## Passo 1 — o teto do oráculo: NÃO

| variante | win rate |
|---|---:|
| **A** — `v006`, coop sempre, sem ponte | **0,2963** (16/54) |
| **B1** — prefixo coop, oráculo escolhe ovelha no d11 se a lã estiver sã | **0,2037** |
| **B2** — prefixo ovelha, oráculo escolhe coop no d11 se a lã estiver morta | **0,1852** |

**Os dois tetos, com conhecimento perfeito do regime, ficam abaixo do `v006`.**

### O custo de transição, medido diretamente

Mesmo regime final, com e sem a ponte:

| regime final | sem ponte | com ponte | custo |
|---|---:|---:|---:|
| ovelha | 0,185 | **0,019** | **−0,167** |
| coop | 0,296 | **0,167** | **−0,130** |

Adiar custa 13 a 17 pontos de win rate **independentemente de qual regime se escolhe**. Um
tape que constrói 18 tiles de pasto e 11 ovelhas até o dia 11 não pode ser iniciado no dia 11:
a economia é uma construção física acumulada, não uma escolha que se toma no momento.

Por subgrupo do oráculo:

| subgrupo | `v006` | ovelha desde d6 | coop→ovelha | ovelha→coop |
|---|---:|---:|---:|---:|
| lã sã (n=17) | 0,294 | **0,471** | 0,000 | 0,412 |
| lã morta (n=37) | 0,297 | 0,054 | 0,027 | 0,054 |

Nos 17 mundos de lã sã, a ovelha **desde o dia 6** ganha 0,471; a mesma ovelha alcançada por
ponte no dia 11 ganha **0,000**. A informação não é o gargalo. A execução é.

### Duas referências que delimitam a direção inteira

| referência | win rate |
|---|---:|
| **B0** — oráculo perfeito **no dia 6**, sem ponte | **0,3519** |
| B3 — melhor-de-todas por mundo (limite superior, não é política) | 0,4074 |

Mesmo um oráculo perfeito no único momento em que o regime é executável vale
**+0,056 nos 54 mundos**, ou **+3 mundos em 67** (0,388 → 0,433).

## Passo 2 — não executado, e por quê

A lógica de promoção declarada é: se o teto do oráculo `B ≤ A`, rejeitar imediatamente e não
construir a regra observável, porque nenhum classificador pode recuperar informação que a
execução não consegue usar. `B1` e `B2` ficam ambos abaixo de `A`. **A regra observável não
foi construída.**

## E o teto de +0,056 também não é alcançável

Testando se "lã sã no dia 15" é previsível a partir de qualquer observável no dia 5 ou 6 —
preços, inventário do mercado, identidade das lojas conhecidas, nosso estado, estado público do
adversário — 52 candidatas, stump raso com limiar no ponto médio, validação leave-one-world-out:

    melhor regra        d5_inv_WHEAT     LOO = 0,639
    nulo de seleção     mediana 0,594    p95 0,647    max 0,685

**A melhor regra fica abaixo do p95 do próprio ruído de seleção.** O alvo tem base 0,448 (30 de
67 mundos com lã sã) e nada observável no turno de roteamento o separa.

## Conclusão

A direção "decidir o regime melhor" está fechada por três medições independentes:

1. adiar o compromisso custa mais do que a informação vale (−0,13 a −0,17 contra +0,056);
2. o teto absoluto com oráculo perfeito no dia 6 é +3 mundos em 67;
3. esse oráculo não é construível — nenhum observável no dia 6 prediz o mercado de lã.

## O que isso implica, e é mais geral do que o experimento

O custo de transição não é uma propriedade da lã. É uma propriedade de **decisões de
construção**: terra, pasto, coop, rebanho. Elas acumulam estado físico e não podem ser
retrofitadas, então qualquer adaptação tardia sobre os tapes do `v006` que mexa nelas paga de
13 a 17 pontos.

Isso não fecha a adaptação em geral. Fecha a adaptação **de build**. Decisões de mercado —
quando vender, quanto, a que preço — não acumulam estado físico e são comutáveis a qualquer
momento sem custo de transição.

E a aritmética aponta para lá: `docs/wide-loss-diagnosis-v006.md` mediu que **+100 moedas
uniformes viram 8 mundos** (0,388 → 0,508), contra os +3 que o regime perfeito valeria. O
próximo experimento é uma decisão de mercado comutável tarde, não outra decisão de regime.
