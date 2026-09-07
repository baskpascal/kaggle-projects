# Revisão do projeto — 7 de setembro de 2026

O projeto faz sentido como laboratório: usa o interpretador oficial, preserva
observações privadas, congela versões, testa paridade de replays e verifica o
artefato pelo loader real. A estratégia atual **ainda não demonstra capacidade
de disputar a ponta**. Ganhar de variantes próprias escondia uma diferença grande
para o adversário público disponível no próprio repositório.

## Escopo verificado

Foram revisados `agent`, `arena`, `eval`, `submission`, testes, dependências,
configuração e o manifesto do adversário público. As regras de execução foram
conferidas no interpretador instalado `1.32.7`, cujo hash é validado pelo projeto,
e no [código oficial publicado](https://github.com/Kaggle/kaggle-environments/tree/master/kaggle_environments/envs/kaggriculture).
O site da competição retornou uma página sem texto pelo navegador desta sessão;
também havia uma cópia local das regras. Esta revisão não pressupõe que o ranking
público tenha sido medido nem que uma submissão tenha sido enviada.

## Problemas corrigidos nesta entrega

| Área | Problema | Correção |
|---|---|---|
| Venda, #3 | Despejo integral ignora receita marginal e recuperação por demanda | Quantidade definida pela curva de preço, limite de retenção, pressão de armazenamento e liquidação final |
| Previsão, #12 | Colheita guardada parece demanda disponível para novos plantios | Galpão e inventários próprios entram na oferta prevista |
| Avaliação, #11 | `champion` muda junto de `challenger` | `champion` resolve o arquivo imutável v000, inclusive no hash |
| Evidência, #11 | Não se mede preço realizado nem estoque descartado | Auditoria dos commits reais de venda e das duas formas de entrega |
| Comparação, #11 | Relatórios separados não garantem comparação pareada | Verificação de seeds, assentos, adversários, configurações, hashes e falhas; intervalos por blocos |
| Documentação, #10 | Instalação, variantes, seeds e submissão ficam implícitos | README com comandos reproduzíveis e limites da avaliação |
| Adversários, #1 | Adversário público nunca é usado | Confrontos nos dois assentos e relatório por adversário |

O cenário da #3 deve ser interpretado corretamente: a penalidade do melão é
quadrática em relação ao estoque de equilíbrio. Com estoque 10000, seis unidades
rendem $1500 pelo modelo oficial; a queda severa exige oferta acumulada. O
controle de vendas trata esse estoque acumulado e a recuperação provável.

As entregas manuais acontecem antes do mercado. As ordens projetam o estoque
depois de `PICKUP`/`DROP`, permitindo vender uma entrega no mesmo turno, inclusive
no último. A venda reserva espaço para inventários e um buffer de colheita; a
auditoria mede a eventual perda, em vez de concluir que zero sobras significa
zero desperdício. Inventário impossível de acomodar não pode ser recuperado por
uma venda posterior ao descarte.

## Achados estratégicos que continuam relevantes

| Prioridade | Achado | Consequência e próximo experimento |
|---|---|---|
| Alta | Diferença para `cok_v10` | Medir vitória contra famílias externas adicionais; melhorar produção e uso de trabalho antes de promover |
| Alta | #7: `planned_crops` é acumulado, mas não retorna a `crop_scores` | Comparar plantio incremental por unidade, com reserva de sementes e diversificação economicamente justificada |
| Alta | #6: não há tarefas `FERTILIZE` | Estimar ganho marginal líquido do fertilizante e da viagem; fertilizar tudo pode custar mais do que rende |
| Alta | Melão usa pico planejado no dia 10, enquanto o pico oficial é 12 | Comparar colheita antecipada com mais rendimento e ocupação; a constante é uma escolha estratégica, não paridade com a tabela oficial |
| Média | #2: pecuária padrão desligada | Avaliar gansos, vacas e ovelhas com custo de alimentação, instalações, trabalho e saturação; ativar globalmente não é uma melhoria demonstrada |
| Média | Seleção de tarefas é gulosa e sem compromisso de rota | Benchmark de atribuição conjunta de trabalhadores, replanejamento e custo de abandono de trajetos |
| Média | #9: margem não é rating | Manter vitória pareada como critério principal; margem serve como diagnóstico de potencial e fragilidade |
| Média | `config.yaml` não é aplicado automaticamente pelo CLI | Faixas e critérios são documentados, mas ainda não há comando que imponha todos os gates de promoção |
| Média | Uma única família externa | Aumentar diversidade de adversários e reservar holdout real para a decisão final |

Nos 80 jogos iniciais, 26,0% das ações do baseline eram `PASS`; isso não prova
isoladamente desperdício, porque pode faltar trabalho rentável. As ações sem
efeito foram zero. Portanto, o próximo ganho provavelmente exige tarefas e
produção melhores, e não apenas corrigir comandos inválidos.

`crop_scores` é uma aproximação de safra finita, não um simulador de retorno
esperado: trata ocupação, quantidade e demanda com constantes; não simula cada
dia de produção, cuidado, fertilização ou decisões do adversário. O estoque
próprio agora entra no modelo, mas esse limite continua.

## Limites da infraestrutura

O `fast` chama o interpretador oficial e tem paridade de replays testada; não é
um sandbox para executar código desconhecido. Adversários locais são código
confiável executado no processo. O preflight verifica loader, temporada completa
e erros; não testa desempenho competitivo. O hash de versão impede misturar
ambientes silenciosamente, mas não detecta sozinho uma atualização remota.

A pasta `.github` apareceu durante o trabalho com mudanças de outra sessão e
foi preservada. Não atribuí esta implementação de CI à presente entrega. As
issues #5 e #8 devem ser avaliadas contra o conteúdo efetivamente publicado.
A decisão de visibilidade da #4 permanece uma decisão do proprietário.

As evidências numéricas e os limites do aceite da venda estão em
[ISSUE3_RESULTS.md](ISSUE3_RESULTS.md). O holdout final não foi utilizado.
