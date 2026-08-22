# Análise Exploratória e Fundamentação do Dashboard

> Documento de ciência de dados: registra as perguntas investigadas, o método, as consultas executadas, os números obtidos e — principalmente — **como cada achado mudou o desenho do produto**. Um gráfico só entrou no dashboard se respondia a uma pergunta operacional e se a análise mostrou que ele não enganaria quem o lê.
>
> Reprodutível: as consultas vivem em [src/api/analytics.py](src/api/analytics.py) e são servidas por `GET /api/v1/analytics/*`.

## 0. Contexto e dados

| Item | Valor |
|---|---|
| Registros | 166.796 |
| Período | 30/04/2026 → 16/06/2026 (~47 dias) |
| Rótulos brutos (`fault`) | 151 → **17 famílias canônicas** via [label_map.yaml](src/etl/label_map.yaml) |
| Features de sensor | 18 brutas + 5 derivadas em [src/ml/features.py](src/ml/features.py) |
| Regimes de rotação | 500 rpm (55.857) · 2000 rpm (55.160) · 1000 rpm (53.414) · 3000 rpm (1.707) · 0 rpm (658) |

**Premissa central que orientou tudo:** o rótulo bruto não identifica apenas a falha — identifica a **campanha de coleta** (`rolamento_inner`, `rolamento_inner_2`, `new_rolamento_inner_0`, `rolamento_inner_carga`…). Isso tem consequências estatísticas severas, exploradas na Análise 4.

---

## 1. A severidade é comparável entre famílias?

**Pergunta.** O time de manutenção pergunta "essa vibração é grave?". A resposta natural seria ranquear as famílias pelo RMS de velocidade. Isso é válido?

**Método.** Mediana e p95 da velocidade RMS por família, primeiro sem condicionar e depois estratificando por regime de rotação.

**Resultado — sem condicionar:**

| família | RMS mediano X (mm/s) | p95 |
|---|---|---|
| polia | 2,77 | 3,61 |
| desbalanceamento | 2,56 | 3,98 |
| ventoinha | 2,53 | 4,35 |
| rolamento_inner | 2,42 | 3,09 |
| desalinhamento | 2,32 | 3,01 |
| cocked_rotor | 2,30 | 3,00 |
| **normal** | **2,29** | 3,13 |
| correia | 2,22 | 2,74 |

**Achado.** A faixa inteira cabe em 2,2–2,8 mm/s — e o estado `normal` fica no meio do ranking. Um gráfico "severidade por família" com esses números diria que operar normalmente é mais grave que uma correia defeituosa. **É artefato de agregação**: as famílias não estão igualmente distribuídas entre os regimes de rotação, e a rotação domina a amplitude.

**Decisão de projeto.** Nenhum painel de severidade agrega regimes de rotação. Toda leitura é estratificada por RPM, e o gráfico oferece a alternativa **relativa à linha de base** (o estado `normal` do mesmo regime), que é a comparação com significado físico.

```sql
-- src/api/analytics.py :: severity_by_rpm
SELECT f.name AS family, f.is_fault, e.rpm, count(*) AS n,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.x_rms_velocity_mm_s) AS x_p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY e.x_rms_velocity_mm_s) AS x_p95,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY e.x_rms_velocity_mm_s) AS x_p99,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.temperature_c)       AS temp_p50
FROM events e JOIN fault_families f ON f.id = e.family_id
WHERE e.rpm > 0 AND e.x_rms_velocity_mm_s IS NOT NULL
GROUP BY f.name, f.is_fault, e.rpm
HAVING count(*) >= 10;
```

Usa-se **mediana e percentis**, não média: as distribuições têm cauda longa e a média seria puxada por transientes de partida.

### Zonas ISO 10816 — apresentadas com ressalva

O dashboard sobrepõe as faixas A/B/C/D da ISO 10816 ([src/core/severity.py](src/core/severity.py)) porque é a linguagem que o mantenedor usa. Duas honestidades acompanham:

1. A classe (I–IV) depende da potência e da fundação da máquina — por isso é **selecionável**, não fixada por nós.
2. Este é um **banco de ensaio com falhas induzidas**: a bancada opera deliberadamente fora da faixa saudável, então a zona absoluta é indicativa. A decisão de intervenção deve usar o desvio contra a própria linha de base.

---

## 2. Os dados obedecem à física dos manuais?

**Pergunta.** O Doc3 (desbalanceamento) afirma **F = m·r·ω²**: a força cresce com o *quadrado* da rotação. Se os dados forem fisicamente coerentes, o desbalanceamento deve escalar com a rotação muito mais rápido que as demais falhas. Isso é verificável — e é um teste de sanidade do dataset inteiro.

**Resultado.** Mediana do RMS por regime:

| rotação | desbalanceamento | linha de base (`normal`) | severidade relativa |
|---|---|---|---|
| 500 rpm | 2,52 | 2,35 | 1,08× |
| 1000 rpm | 2,11 | 2,05 | 1,03× |
| 2000 rpm | 3,45 | 2,66 | 1,30× |
| **3000 rpm** | **7,50** | 2,83 | **2,65×** |

Demais falhas a 3000 rpm ficam entre 2,5 e 3,4 mm/s.

**Achado.** A previsão do manual se confirma: o desbalanceamento é indistinguível do normal em baixa rotação e dispara em alta. Nenhuma outra família apresenta esse comportamento.

**Decisão de projeto.** Virou painel próprio ("Validação física"), com as demais famílias em cinza como referência. Ele cumpre dois papéis: valida a qualidade do dataset e demonstra entendimento do domínio, não apenas correlação.

**Implicação diagnóstica** (registrada como trabalho futuro): a assinatura útil do desbalanceamento não é o RMS, é a **derivada do RMS em relação à rotação**. Uma feature do tipo `d(RMS)/d(ω²)` por máquina provavelmente separaria essa falha muito melhor do que qualquer indicador instantâneo.

---

## 3. O que realmente distingue cada falha?

**Pergunta.** Os manuais associam falhas a assinaturas específicas (curtose e fator de crista altos para impactos de rolamento; 1×RPM para desbalanceamento; 1×+2× para desalinhamento). Essas assinaturas aparecem nestes dados?

**Método.** Duas medidas complementares:

- **Assinatura** — z-score da média de cada família contra a média global: `z = (média_família − média_global) / desvio_global`. Padroniza escalas heterogêneas (°C, g, mm/s, Hz) numa mesma régua.
- **Poder discriminativo** — razão entre a variância *entre* famílias e a variância *dentro* das famílias, no espírito do F de ANOVA:

```
        Σ nᵢ · (médiaᵢ − média_global)² / (k − 1)
  F =  ───────────────────────────────────────────
            Σ (nᵢ − 1) · variânciaᵢ / (N − k)
```

Valor alto = a feature separa; valor baixo = a variação é ruído interno.

```sql
-- src/api/analytics.py :: fault_signatures (esquema; gerado para as 18 features)
SELECT f.name, count(*) AS n, avg(col) AS m_col, var_samp(col) AS v_col
FROM events e JOIN fault_families f ON f.id = e.family_id
GROUP BY f.name HAVING count(*) >= 30;

SELECT count(*) AS n, avg(col) AS g_col, stddev_samp(col) AS s_col
FROM events WHERE family_id IS NOT NULL;
```

**Resultado.**

| feature | razão F |
|---|---|
| vel RMS X | 1.193,6 |
| vel pico X | 1.193,6 |
| freq pico X | 1.106,8 |
| temperatura | 885,3 |
| … | … |
| rotação | 208,3 |
| **curtose Z** | **189,2** |
| acel pico Z | 188,2 |

**Achados.**

1. **Curtose e fator de crista não discriminam.** Curtose fica em 2,65–2,75 e o fator de crista em 3,73–3,85 em *todas* as famílias — inclusive as de rolamento, justamente onde os manuais previam impactos. Causa provável: as métricas vêm pré-agregadas pelo sensor em janelas longas, que suavizam os impulsos que a curtose deveria capturar.
2. **`vel RMS X` e `vel pico X` têm F idêntico (1.193,6)** — são colineares (pico ≈ RMS × fator de crista, e o fator de crista é quase constante). Uma das duas é redundante no modelo.
3. **A frequência do pico é quase degenerada:** 61 Hz responde por 48,7% dos registros (81.194 de 166.796). A feature derivada "ordem do pico" (freq ÷ freq_rotação) fica presa em 3,66 para quase todas as famílias a 1000 rpm — ou seja, **as assinaturas de ordem (1×, 2× RPM) dos manuais não são recuperáveis** a partir de um único valor de pico. Precisariam do espectro completo (FFT bruta), ausente do dataset.

**Decisão de projeto.** O painel de assinaturas mostra o heatmap **ordenado pelo poder discriminativo** e marca em cinza os indicadores fracos, com legenda explicando o descompasso frente aos manuais. Esconder isso seria mais bonito e menos verdadeiro.

---

## 4. Por que o modelo cai de 0,89 para 0,14? (o achado principal)

**Contexto.** O treino ([src/ml/train.py](src/ml/train.py)) reporta dois protocolos:

| protocolo | acurácia | F1 macro |
|---|---|---|
| Split aleatório estratificado | 0,895 | 0,862 |
| **Holdout por sessão de coleta** | **0,137** | **0,195** |

A explicação usual — "leituras da mesma sessão são near-duplicatas" — é verdadeira mas insuficiente: não diz *qual mecanismo* carrega o vazamento.

**Hipótese.** A `temperature_c` é a feature mais importante do LightGBM (11,7%). Temperatura ambiente não é sintoma mecânico de rotor inclinado. A hipótese é que ela funcione como **relógio da campanha de coleta**: cada sessão foi gravada em um momento distinto, com temperatura própria; como cada sessão carrega um único rótulo, "temperatura ≈ 25,3 °C" prediz o rótulo sem qualquer conteúdo físico.

**Teste.** Decomposição da variância da temperatura entre e dentro das sessões:

```sql
-- src/api/analytics.py :: leakage_evidence
WITH per_session AS (
    SELECT raw_fault, avg(temperature_c) AS m, stddev_samp(temperature_c) AS s
    FROM events WHERE raw_fault IS NOT NULL
    GROUP BY raw_fault HAVING count(*) >= 30
)
SELECT stddev_samp(m) AS entre_sessoes,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s) AS dentro_da_sessao
FROM per_session;
```

**Resultado (146 campanhas com ≥30 eventos):**

| | desvio-padrão |
|---|---|
| **entre** as médias das campanhas | 2,70 °C |
| **dentro** de cada campanha (mediana) | 0,31 °C |
| **razão** | **8,8×** |

Confirmação direta — a mesma família em campanhas diferentes:

```
rolamento_inner → new_..._0: 19,5 °C | new_..._2: 18,0 °C | _2: 25,3 °C
cocked_rotor    → adxl_0: 21,9 °C | cocked_rotor: 22,6 °C | _pos_2: 26,9 °C
```

**Conclusão.** Hipótese confirmada. A temperatura identifica a campanha, não a falha. O modelo aprendeu um atalho que funciona quando treino e teste compartilham campanhas (split aleatório) e desaba quando não compartilham (holdout por sessão). O gap entre os protocolos **não é ruído nem azar de amostragem — é este vazamento**.

### Teste de ablação — onde a hipótese foi parcialmente refutada

Se a temperatura *causasse* o gap, removê-la deveria recuperar generalização. O treino passou a registrar `eval_session_holdout_no_temp` — mesmo protocolo por sessão, sem a temperatura no vetor:

| protocolo | acurácia | F1 macro |
|---|---|---|
| Holdout por sessão (completo) | 0,137 | 0,195 |
| Holdout por sessão **sem temperatura** | 0,129 | **0,165** |

**Removê-la piorou o resultado.** A medição do carimbo de sessão continua válida, mas a inferência causal "a temperatura causa o gap" está errada — e registrar isso vale mais do que um resultado bonito.

**Por que remover não resolve.** Aplicando a mesma decomposição de variância às 18 features:

| feature | razão entre/dentro | | feature | razão |
|---|---|---|---|---|
| temperatura | 8,8× | | curtose X | 4,1× |
| acel pico Z | 5,7× | | acel HF X | 4,1× |
| curtose Z | 5,7× | | vel RMS Z | 3,6× |
| acel pico X | 5,6× | | … | … |
| | | | freq pico X | 0,7× |
| | | | rotação | 0,6× |

**16 das 18 features têm razão > 1** — quase todo o vetor identifica parcialmente a campanha. Retirar a mais extrema só faz o modelo migrar para a próxima proxy, e ainda perde o sinal físico legítimo que a temperatura carrega (aquecimento de mancal é sintoma real, segundo os manuais).

**Conclusão revista.** O gap 0,89 → 0,14 não decorre de uma feature específica: decorre de cada condição ter sido coletada em uma **única campanha**, o que confunde `sessão` com `rótulo` em todo o espaço de features. Nenhuma seleção de features corrige isso — é limitação do desenho experimental, e a correção é de coleta, não de modelagem.

**F1 por classe no holdout por sessão** — o que generaliza e o que não:

| classe | F1 | leitura |
|---|---|---|
| falta_fase | 0,940 | única que generaliza de verdade |
| motor_desligado | 0,546 | estado, não falha (assinatura trivial) |
| correia / desalinhamento / polia | 0,25–0,28 | sinal fraco porém real |
| rolamento_inner / cocked_rotor | 0,019 / 0,005 | não generaliza |

**Decisão de projeto.** O painel "Qualidade do modelo" expõe os três protocolos lado a lado, a importância das features com a temperatura destacada e o gráfico de vazamento por feature. É deliberado: um dashboard que mostrasse apenas 89% de acurácia seria uma promessa que o sistema não cumpre em produção.

**Encaminhamentos propostos:**

1. **Coletar múltiplas campanhas por condição** — única correção que ataca a causa. Sem isso, qualquer métrica de generalização fica limitada por construção.
2. **Normalizar contra a linha de base da própria máquina** em vez de remover features: substituir valores absolutos pelo desvio contra a referência recente do equipamento (disponível em produção, ao contrário da identidade da campanha). Remove o offset de sessão preservando o sinal.
3. Adotar features **invariantes à condição operacional**: razões entre eixos e entre bandas, e a derivada RMS×ω² da Análise 2.
4. Com acesso à forma de onda, extrair envelope/FFT — sem isso as frequências características (BPFO/BPFI/BSF) dos manuais são inalcançáveis.
5. Enquanto o teto de generalização for esse, **a busca por similaridade (KNN) é a resposta operacional mais defensável**: em vez de afirmar uma classe, ela mostra ocorrências próximas do histórico e deixa a decisão com o técnico — que é exatamente o que o edital pede.

---

## 5. O que documentar primeiro?

**Pergunta.** O sistema só prescreve correção para falhas com documento. Quais lacunas custam mais caro?

**Método.** Cruzar frequência (contagem por família) com severidade (máxima severidade relativa entre regimes, da Análise 1) e cobertura documental (famílias com chunks no ChromaDB).

**Resultado.** 9 das 12 famílias de falha estão documentadas (268 chunks dos Doc1–Doc6). Sem procedimento:

| família | ocorrências | situação |
|---|---|---|
| **eccentric_rotor** | **16.497** | maior lacuna — 2ª família mais frequente |
| ventoinha | 12.299 | sem documento |
| falta_fase | 800 | sem documento, mas é a classe que o modelo melhor reconhece |

**Decisão de projeto.** Matriz de priorização (bolhas: frequência × severidade, cor = cobertura). O canto superior direito em laranja é a fila de trabalho da engenharia de manutenção. `eccentric_rotor` é o item nº 1.

---

## 6. Decisões de visualização (e um erro evitado)

As cores não foram escolhidas por gosto — cada conjunto passou por um validador de paleta (banda de luminosidade, croma, separação para daltonismo, contraste contra a superfície) nos modos claro e escuro. Registro em [src/ui/theme.py](src/ui/theme.py).

| Codificação | Escolha | Justificativa |
|---|---|---|
| documentada × sem documento | azul `#2a78d6` / laranja `#eb6834` | a escolha intuitiva verde/vermelho **falhou** o teste: ΔE 4,1 em deuteranopia (piso 6). Azul/laranja atinge ΔE 24,7. A legenda usa ✓/✗ como codificação secundária |
| regime de RPM | rampa ordinal de um só tom (azul, claro→escuro) | RPM é grandeza **ordenada**, não identidade — hues categóricos sugeririam categorias sem ordem |
| z-score | divergente azul ↔ vermelho, cinza no meio | z-score é **polaridade** (acima/abaixo da média); arco-íris seria ilegível |
| famílias no gráfico de física | cinza + 1 destaque | 12 séries excedem qualquer paleta segura; destaque sobre referência cinza comunica melhor |

---

## 7. Limitações desta análise

- **Uma única máquina.** Todos os dados vêm de uma bancada; nada aqui foi validado entre equipamentos distintos.
- **Métricas pré-agregadas.** Sem forma de onda bruta, análise de envelope e frequências características de rolamento são impossíveis.
- **Rótulos por campanha.** Não há repetição independente da mesma condição em campanhas diferentes para a maioria das famílias — o teto de generalização é imposto pelo desenho experimental.
- **Janela curta (47 dias).** Não há tendência de degradação de longo prazo observável; a distribuição temporal reflete o cronograma de ensaios, não a vida do equipamento.
- **Sem custo por falha.** A priorização usa frequência × severidade vibracional como proxy; com custo de parada e criticidade do ativo, ficaria melhor.

## 8. Como reproduzir

```bash
docker compose up -d postgres            # banco
python scripts/ingest_data.py            # 166.796 eventos
python -m src.ml.train                   # métricas em artifacts/metrics.json
uvicorn src.api.main:app --port 8000     # /api/v1/analytics/*
streamlit run src/ui/app.py              # dashboard
pytest tests/test_analytics.py           # valida as agregações
```

As agregações rodam em ~0,2 s sobre os 166 mil eventos, por isso são calculadas sob demanda em SQL em vez de pré-computadas — o dashboard reflete inclusive os eventos que chegam por MQTT durante a demonstração.
