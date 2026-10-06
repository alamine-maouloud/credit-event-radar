# Credit Event Radar · Spécification complète

**Auditable AI-assisted credit monitoring**
Version 1.1 · octobre 2026 · Auteur : Al-Amine Sidick Maouloud

> Amendement 1.1 (2026-10-06) : les notations par agence font foi pour les événements crédit. La notation composite (§9.2) devient une métadonnée analytique et ne déclenche plus aucune règle. RAT-01, RAT-02, RAT-08 et MOD-01 sont réécrites en conséquence (§9.3, §9.4). Les types de notation deviennent explicites et seuls les types émetteur alimentent le composite. Le seed suit une échelle de validation en cinq niveaux. Les phases s'exécutent dans l'ordre 2, 4, 3. Voir `docs/ARCHITECTURE.md`, ADR-001 à ADR-004.

---

## 0. Résumé

Credit Event Radar est un prototype de surveillance crédit pour une équipe de gestion obligataire. Il :

1. **détecte** les événements crédit sur une watchlist d'émetteurs IG/HY (actions de notation, publications de résultats, nouvelles émissions) ;
2. les **priorise** en P1/P2/P3 avec un moteur de règles déterministe et documenté ;
3. les **explique** dans une synthèse FR/EN où chaque phrase est reliée à son passage source ;
4. **vérifie** chaque affirmation et exclut ce qui n'est pas soutenu par une source ;
5. **alerte** l'analyste (Teams, email ou rendu local) avec un panneau « Why this priority? ».

L'analyste humain décide. L'outil ne produit ni recommandation d'investissement ni prévision de spread.

---

## 1. Contexte et problématique

### 1.1 Contexte de marché (source publique)

Dans sa stratégie trimestrielle Fixed Income de juillet 2026, Rothschild & Co Asset Management indique que des primes de risque historiquement basses rendent la sélection des émetteurs plus importante. L'équipe adopte une approche plus prudente sur le High Yield, considère les émetteurs financiers comme un moteur clé de performance, et suit de près les besoins de financement des hyperscalers liés à l'IA, ainsi que leur capacité à convertir ces dépenses en croissance rentable.

> Dans la case study, cite cette publication avec sa date et son lien, et paraphrase-la. Ne la recopie pas.

### 1.2 Conséquence opérationnelle

Quand le marché rémunère peu le risque, l'avantage se joue sur la vitesse et la qualité de l'analyse émetteur par émetteur. Or les analystes et gérants doivent absorber un flux important : screening IG/HY, newsflow, résultats, actions de rating, nouvelles émissions, concurrents. Une partie de leur temps sert à trier l'information plutôt qu'à l'analyser.

### 1.3 Besoin explicite

L'offre de stage vise à accélérer l'intégration d'outils IA dans le workflow des analystes crédit et des gérants. Elle mentionne notamment des alertes intelligentes Teams/Email sur les événements de crédit et une bibliothèque de prompts FR/EN normalisés.

### 1.4 Problématique du projet

> Comment détecter automatiquement les événements crédit importants, les hiérarchiser selon leur matérialité, et fournir immédiatement à l'analyste une synthèse exploitable, tout en garantissant que chaque information reste traçable jusqu'à sa source ?

### 1.5 Critères de succès

| Critère | Mesure | Cible MVP |
|---|---|---|
| Ne manquer aucun événement critique | Recall@P1 (k/n avec IC 95 % de Wilson) | n/n sur le jeu gold |
| Limiter le bruit | Précision globale, fausses alertes par émetteur et par jour (run live) | À mesurer, puis réduire |
| Réactivité | Délai médian entre publication et alerte (run live) | Borné par l'intervalle de polling |
| Traçabilité | Part des claims `VERIFIED` dans les notes publiées | 100 % des claims publiés |
| Utilité | Temps pour obtenir une fiche émetteur relisible | Quelques minutes |

Toutes les valeurs affichées proviennent d'exécutions réelles de `eval/`.

---

## 2. Positionnement

| L'outil fait | L'outil ne fait pas |
|---|---|
| Détecter, classer, prioriser, expliquer, sourcer, alerter | Recommander d'acheter ou de vendre |
| Proposer des points à vérifier pour l'analyste | Prédire des spreads ou des prix |
| Rendre chaque décision de priorité explicable | Laisser un LLM décider de la priorité |
| Signaler ce qui n'est pas sourcé | Remplacer l'analyste |

Nom public : **Credit Event Radar · Auditable AI-assisted credit monitoring**.
À éviter : « AI Credit Analyst », « Autonomous Investment Agent ».

---

## 3. Utilisateurs et user stories

**Analyste crédit** (couvre un ou plusieurs secteurs)
- En tant qu'analyste, je reçois immédiatement une alerte quand un émetteur de ma couverture subit une action de notation qui le rapproche de la frontière IG/HY.
- En tant qu'analyste, je comprends en un clic pourquoi une alerte est P1, et quelles règles l'ont déclenchée.
- En tant qu'analyste, j'obtiens un brouillon de note de comité FR ou EN où chaque phrase renvoie à sa source.

**Gérant de portefeuille**
- En tant que gérant, je reçois un digest des événements P2/P3 sans être interrompu.
- En tant que gérant, je vois les nouvelles émissions significatives (taille, devise, maturité, séniorité) de la watchlist.

**Responsable de l'équipe ou de la conformité**
- En tant que responsable, je peux auditer chaque alerte : documents sources, hash, modèle, version de prompt, règles déclenchées.

---

## 4. Périmètre

### 4.1 MVP (MoSCoW)

**Must**
- Watchlist de 10 à 15 émetteurs (voir §5)
- Trois familles d'événements : notation, résultats, nouvelles émissions
- Connecteurs : SEC EDGAR (émetteurs US), communiqués IR des émetteurs (RSS ou pages), news RSS si conditions d'utilisation compatibles
- Extraction structurée (EDGAR) et extraction LLM avec schéma strict et validation des spans
- Moteur de matérialité déterministe (`rules.yaml`) avec notation composite et modificateurs
- Panneau « Why this priority? »
- Synthèse et note de comité FR/EN avec citations vérifiées
- Alertes : rendu local (HTML/JSON) obligatoire, Teams et email si configurés
- Journal d'audit SQLite
- Bibliothèque de prompts versionnée
- Benchmark sur jeu gold (60 événements et 30 documents de bruit au minimum)
- Comparaison de 2 LLM sur la même bibliothèque de prompts

**Should**
- Module « Capex IA vs FCF » pour les hyperscalers à partir de l'API XBRL de la SEC
- Viewer Streamlit minimal (liste d'alertes, panneau « Why? », citations cliquables)
- Run live de 5 à 7 jours
- Extension du jeu gold à 100 événements et 40 documents de bruit

**Could**
- Contexte de marché : indices de spreads OAS publics (historique potentiellement limité selon la source)
- Troisième LLM dans le benchmark
- Double annotation de 20 % du jeu gold par une seconde personne

**Won't (roadmap uniquement)**
- Power BI, intégration Bloomberg, données internes, ESG, prédiction de spreads, RAG avancé, agent autonome, déploiement en production

### 4.2 Limites assumées

- Pas de Bloomberg : les données sont publiques et gratuites. Une couche « source » interchangeable prépare l'intégration future.
- Les communiqués des agences de notation sont souvent derrière un login ou sous licence restrictive. Vérifie leurs conditions d'usage. À défaut, passe par les communiqués des émetteurs (qui annoncent souvent leurs changements de notation) et par le flux de news.
- Les dépôts réglementaires européens sont fragmentés. Pour les émetteurs européens, le MVP s'appuie sur les communiqués IR.

---

## 5. Univers de démo (Demo watchlist)

### 5.1 Règles de construction

1. Source prioritaire : les principales lignes publiées dans les reportings publics des fonds crédit de Rothschild & Co AM, s'ils sont accessibles (par exemple R-co Conviction Credit Euro et la gamme R-co Target). Note la date de récupération.
2. Ajoute les hyperscalers, thème central de leur stratégie trimestrielle.
3. Complète par des émetteurs des secteurs couverts par leurs analystes : financières, automobile, chimie, pharmacie, utilities, pétrole et gaz, consommation.
4. Inclus au moins 2 émetteurs proches de la frontière IG/HY (BBB-/BB+), sinon le moteur de matérialité n'a rien d'intéressant à montrer.
5. Formulation publique obligatoire : *Demo universe constructed exclusively from publicly disclosed holdings and publicly available R&Co Asset Management research. Portfolio disclosures retrieved as of DD/MM/2026.*

### 5.2 Liste de départ indicative

À confirmer ou remplacer selon les reportings publics.

| Groupe | Émetteurs | Source principale |
|---|---|---|
| Hyperscalers (US) | Microsoft, Alphabet, Meta, Amazon, Oracle | SEC EDGAR + IR |
| Automobile | Volkswagen, Stellantis | IR |
| Chimie / Pharmacie | BASF, Bayer | IR |
| Utilities | Engie ou Veolia | IR |
| Pétrole et gaz | TotalEnergies | IR (+ 6-K SEC) |
| Financières / Subfin | BNP Paribas, Deutsche Bank | IR |

### 5.3 Format `config/universe.yaml`

```yaml
issuers:
  - id: VOLKSWAGEN
    name: Volkswagen AG
    aliases: ["Volkswagen", "VW Group", "Volkswagen Group", "VW AG"]
    sector: automotive
    country: DE
    sec_cik: null
    ir_feeds:
      - url: "<URL du flux ou de la page communiqués>"
        type: rss          # rss | html
    tags: [demo_watchlist]
```

### 5.4 Format `data/seeds/ratings_seed.csv`

```
issuer_id,agency,rating,outlook,watch,as_of,source_url,retrieved_at
VOLKSWAGEN,SP,<rating>,<outlook>,<none|negative|positive>,<YYYY-MM-DD>,<url>,<YYYY-MM-DD>
```

Chaque ligne est vérifiée à la main à partir d'une source publique (page « credit ratings » des relations investisseurs, par exemple). **Aucune notation n'est inventée ni complétée par un LLM.**

---

## 6. Sources de données

| Source | Usage | Accès | Contraintes |
|---|---|---|---|
| SEC EDGAR Submissions API (`data.sec.gov/submissions/CIK##########.json`) | Liste des dépôts : 8-K, 6-K, 424B2/424B5, FWP | Gratuit | User-Agent avec contact, moins de 10 requêtes par seconde |
| SEC EDGAR, documents des dépôts | Texte des 8-K, term sheets (FWP), prospectus | Gratuit | Idem |
| SEC XBRL Company Facts API (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`) | Capex, cash-flow opérationnel, dette, émissions de dette | Gratuit | Les tags XBRL varient selon l'émetteur : prévoir des tags de repli |
| Communiqués IR des émetteurs | Résultats, guidance, émissions, annonces de notation | Gratuit | Respecter robots.txt et conditions d'utilisation |
| News RSS | Newsflow, actions de notation relayées | Selon le fournisseur | Vérifier les conditions d'utilisation avant usage |
| Indices OAS publics (Could) | Contexte de spreads IG/HY | Gratuit | Historique potentiellement limité |

### 6.1 Mapping déterministe EDGAR → événements

| Signal EDGAR | Événement | Remarque |
|---|---|---|
| 8-K Item 2.02 | Publication de résultats | |
| 8-K Item 2.03 | Création d'une obligation financière directe | Nouvelle dette |
| 8-K Item 2.04 | Événement déclenchant une accélération d'obligation | Candidat P1 direct |
| 8-K Item 2.06 | Dépréciation significative | |
| 8-K Item 1.01 / 2.01 | Accord significatif, acquisition | Extension M&A |
| 8-K Item 5.02 | Départ ou nomination de dirigeants | Extension management |
| 424B2 / 424B5 / FWP | Nouvelle émission obligataire | Les FWP contiennent souvent le term sheet final |

---

## 7. Modèle de données

### 7.1 Schémas pydantic principaux

```python
class RawDocument(BaseModel):
    doc_id: str                 # sha256 du contenu normalisé
    source_type: Literal["edgar", "ir_feed", "news_rss", "manual"]
    url: HttpUrl
    title: str | None
    published_at: datetime | None
    retrieved_at: datetime
    content_hash: str
    text: str
    raw_path: str               # snapshot brut sur disque
    issuer_hint: str | None

class EvidenceSpan(BaseModel):
    doc_id: str
    char_start: int
    char_end: int
    quote: str
    match_score: float          # rapidfuzz, après normalisation

class CreditEvent(BaseModel):
    event_id: str
    issuer_id: str
    family: Literal["rating", "earnings", "issuance", "other"]
    event_type: str             # ex. downgrade, outlook_change, watch, guidance_cut, new_issue...
    effective_date: date | None
    fields: dict                # champs typés selon event_type (voir 7.2)
    evidence: list[EvidenceSpan]
    extraction_method: Literal["structured", "llm_validated"]
    source_doc_ids: list[str]

class PriorityDecision(BaseModel):
    event_id: str
    priority: Literal["P1", "P2", "P3"]
    triggered_rules: list[str]      # ex. ["RAT-02", "MOD-01"]
    rule_details: list[str]         # explication lisible par règle
    rules_version: str
    llm_role: str                   # ex. "field extraction (validated against source text)"

class Claim(BaseModel):
    claim_id: str
    text: str
    lang: Literal["fr", "en"]
    citations: list[EvidenceSpan]
    status: Literal["VERIFIED", "PARTIAL", "UNSUPPORTED"]
    checks: dict                    # quote_found, numbers_match, support_judgment
```

### 7.2 Champs par type d'événement

- **Rating** : `agency`, `old_rating`, `new_rating`, `old_outlook`, `new_outlook`, `watch`, `scope` (émetteur ou instrument), `instrument_seniority`
- **Earnings** : `period`, `guidance_metric`, `guidance_old`, `guidance_new`, `guidance_change_pct`, `flags` (liquidity, going_concern, covenant, impairment)
- **Issuance** : `amount`, `currency`, `amount_eur_equiv`, `coupon`, `maturity`, `seniority` (senior, subordinated, hybrid, AT1, T2), `use_of_proceeds`

### 7.3 Tables SQLite

`issuers`, `issuer_aliases`, `ratings`, `documents`, `events`, `event_evidence`, `priority_decisions`, `claims`, `notes`, `alerts`, `llm_calls`, `prompt_versions`, `audit_log`, `eval_runs`.

---

## 8. Pipeline

```
[Connecteurs] → [Snapshot + hash] → [Déduplication] → [Rattachement émetteur]
      → [Extraction : structurée | LLM + validation] → [Moteur de matérialité]
      → [Contextualisation LLM] → [Vérification des claims] → [Note FR/EN]
      → [Routage des alertes P1/P2/P3] → [Journal d'audit]
```

1. **Ingestion.** Chaque connecteur implémente `fetch(since) -> list[RawDocument]`. Le contenu brut est stocké sur disque, hashé, et le texte normalisé est extrait (HTML via trafilatura, PDF via PyMuPDF).
2. **Déduplication.** Documents identiques par hash. Événements identiques par la clé (`issuer_id`, `event_type`, agence s'il y en a une, `effective_date` ± 2 jours, champs clés). Les sources sont fusionnées.
3. **Rattachement émetteur.** Table d'alias déterministe d'abord. Si l'émetteur reste ambigu, le document est marqué `unresolved` et n'est jamais deviné.
4. **Extraction.**
   - Voie structurée : codes EDGAR (§6.1) et term sheets.
   - Voie LLM : prompt d'extraction avec schéma JSON strict, puis validation déterministe. Une notation doit exister dans `rating_scales.yaml`. Chaque champ doit être soutenu par une `EvidenceSpan` retrouvée dans le texte (score rapidfuzz ≥ 90 après normalisation). Chaque nombre extrait doit apparaître dans la span. Un champ non validé est rejeté et journalisé.
5. **Matérialité.** Application de `rules.yaml` (§9). Fonction pure : mêmes entrées, même sortie.
6. **Contextualisation.** Le LLM rédige un résumé et une section « pourquoi c'est important pour le crédit », **uniquement** à partir des documents fournis et des fondamentaux disponibles. Chaque phrase porte ses citations.
7. **Vérification.** Voir §10.
8. **Note de comité.** Rendu Jinja2 en Markdown puis HTML, en FR et en EN. Seuls les claims `VERIFIED` sont publiés. Les claims `PARTIAL` sont marqués, et les claims `UNSUPPORTED` sont exclus et listés en annexe d'audit.
9. **Alertes.** Voir §11.
10. **Audit.** Chaque étape écrit dans `audit_log`.

---

## 9. Moteur de matérialité

### 9.1 Échelles de notation (`config/rating_scales.yaml`)

| Notch | S&P / Fitch | Moody's | Catégorie |
|---|---|---|---|
| 1 | AAA | Aaa | IG |
| 2-4 | AA+, AA, AA- | Aa1, Aa2, Aa3 | IG |
| 5-7 | A+, A, A- | A1, A2, A3 | IG |
| 8-10 | BBB+, BBB, BBB- | Baa1, Baa2, Baa3 | IG |
| 11-13 | BB+, BB, BB- | Ba1, Ba2, Ba3 | HY |
| 14-16 | B+, B, B- | B1, B2, B3 | HY |
| 17-19 | CCC+, CCC, CCC- | Caa1, Caa2, Caa3 | HY |
| 20-21 | CC, C | Ca, C | HY |
| 22 | D / SD / RD | · | Défaut |

Frontière IG/HY : notch 10 (BBB-/Baa3) contre notch 11 (BB+/Ba1).

### 9.2 Notation composite

Un émetteur ne devient pas forcément HY parce qu'une seule agence le dégrade. Le moteur calcule une notation composite configurable :

- `middle` : médiane si 3 notations, la plus basse si 2, l'unique si 1 ;
- `average` : moyenne des notches disponibles, avec une convention d'arrondi documentée (par défaut, arrondi vers la notation la plus faible).

> Les grands fournisseurs d'indices utilisent ce type de composite, avec des méthodes différentes. Vérifie leurs méthodologies publiques avant de citer un fournisseur précis dans la case study.

> Amendement 1.1 : le composite est une métadonnée analytique (tri, tableau de bord, en-tête de note). Il ne déclenche aucune règle de §9.3 ni aucun modificateur de §9.4 (ADR-001). Il n'est calculé qu'à partir de lignes GOLDEN du seed et de types de notation émetteur (ADR-002, ADR-003).

### 9.3 Règles de base

| ID | Famille | Condition | Priorité |
|---|---|---|---|
| RAT-01 | Notation | Une notation d'agence passe de IG à HY (fallen angel au niveau agence) | P1 |
| RAT-02 | Notation | Une agence dégrade vers HY alors qu'au moins une autre agence maintient l'émetteur en IG (notation partagée, risque de fallen angel) | P1 |
| RAT-03 | Notation | Dégradation de 2 crans ou plus en une seule action | P1 |
| RAT-04 | Notation | Mise sous surveillance négative d'une notation agence à BBB-/Baa3 | P1 |
| RAT-05 | Notation | Dégradation d'un cran (hors RAT-02) | P2 |
| RAT-06 | Notation | Perspective passant de stable ou positive à négative | P2 |
| RAT-07 | Notation | Mise sous surveillance négative (hors RAT-04) | P2 |
| RAT-08 | Notation | Une notation d'agence passe de HY à IG (rising star au niveau agence) | P2 |
| RAT-09 | Notation | Amélioration de notation ou perspective positive | P3 |
| RAT-10 | Notation | Affirmation sans changement | P3 |
| ERN-01 | Résultats | Langage de liquidité, going concern, bris ou waiver de covenant (span vérifiée) | P1 |
| ERN-02 | Résultats | Baisse de guidance supérieure ou égale au seuil (`guidance_cut_p1_pct`, par défaut 10 %) sur CA, EBITDA ou FCF, ou baisse qualifiée de significative par l'émetteur | P1 |
| ERN-03 | Résultats | Baisse de guidance sous le seuil, ou dépréciation significative | P2 |
| ERN-04 | Résultats | Publication conforme à la guidance | P3 |
| ISS-01 | Émission | Nouvelle émission supérieure ou égale au seuil (`issuance_p2_eur`, par défaut 1 Md EUR équivalent) | P2 |
| ISS-02 | Émission | Émission subordonnée, hybride, AT1 ou T2 | P2 |
| ISS-03 | Émission | Non-call d'un AT1 ou d'un hybride (Should, financières) | P1 |
| ISS-04 | Émission | Émission sous le seuil, tap, refinancement courant | P3 |
| EDG-01 | EDGAR | 8-K Item 2.04 (accélération d'obligation) | P1 |

Note : « EBITDA miss » face au consensus est exclu du MVP, car les données de consensus ne sont pas gratuites. La comparaison se fait avec la guidance publiée ou le trimestre précédent.

### 9.4 Modificateurs

| ID | Condition | Effet |
|---|---|---|
| MOD-01 | Événement négatif sur un émetteur dont la notation d'agence la plus faible est à BBB-/Baa3 | +1 niveau (P3 → P2, P2 → P1) |
| MOD-02 | Au moins 2 événements négatifs P2 sur le même émetteur en 30 jours | Le dernier passe en P1 |
| MOD-03 | Levier vérifié au-dessus du seuil configuré (uniquement si la donnée est disponible et sourcée) | +1 niveau pour un événement négatif |

Règles d'application : la priorité finale est le maximum entre la règle de base et l'effet des modificateurs, plafonnée à P1. Aucun modificateur ne baisse une priorité. Toutes les règles et tous les modificateurs déclenchés sont listés dans `triggered_rules`. Les seuils vivent dans `rules.yaml`, versionné.

### 9.5 Panneau « Why this priority? »

```
Priority: P1
Issuer: ISSUER_TEST_A · Demo watchlist issuer
Event: S&P downgrade BBB- → BB+ (composite remains BBB-)

Triggered rules:
  ✓ RAT-02  Agency downgrade from BBB- to HY while composite stays IG (fallen-angel risk)
  ✓ MOD-01  Negative event on issuer at the IG/HY boundary

Rules version: rules.yaml v1.0
Priority decided by: deterministic rules engine
LLM used for: field extraction (validated against source text), event summary
Sources: [1] <title> · <url> · published <timestamp> · retrieved <timestamp>
```

---

## 10. Traçabilité et vérification des claims

Chaque claim d'une note suit la chaîne : **claim → source → passage → timestamp → statut de vérification**.

Vérification en quatre contrôles :

1. **Citation présente** : au moins une `EvidenceSpan`.
2. **Passage retrouvé** : la citation existe dans le texte source (correspondance exacte ou rapidfuzz ≥ 90 après normalisation).
3. **Cohérence numérique** : chaque nombre du claim (montant, pourcentage, date, notation) apparaît dans le passage cité. Contrôle déterministe par regex.
4. **Soutien du passage** : prompt `verification/claim_support` qui ne voit que le claim et le passage, et répond `SUPPORTED`, `PARTIALLY_SUPPORTED` ou `NOT_SUPPORTED` avec justification.

Statut final :
- `VERIFIED` : les quatre contrôles passent ;
- `PARTIAL` : contrôles 1 à 3 passent, contrôle 4 partiel ;
- `UNSUPPORTED` : tout autre cas. Le claim est exclu de la note et listé dans l'annexe d'audit sous la mention « Unsupported claim · excluded from committee note ».

Pas de score de confiance auto-déclaré par le LLM.

---

## 11. Alertes

### 11.1 Routage (configurable)

| Priorité | Canal | Délai |
|---|---|---|
| P1 | Teams + email + local | Immédiat |
| P2 | Digest Teams/email + local | Toutes les 4 heures |
| P3 | Digest quotidien + local | Une fois par jour |

### 11.2 Teams

Envoi d'une Adaptive Card via un webhook Teams. Privilégie l'app Workflows de Teams, car les anciens connecteurs Office 365 sont en cours de retrait. Contenu : badge de priorité coloré, émetteur, titre de l'événement, 3 faits clés sourcés, règles déclenchées, liens vers les sources, boutons « Note FR » et « Note EN » vers le viewer.

### 11.3 Email

SMTP configurable dans `.env`. Corps HTML identique à la carte.

### 11.4 Local (toujours actif)

`outputs/alerts/<date>/<event_id>.json` et `.html`. Ce rendu est indispensable pour la démo sans tenant Teams.

---

## 12. Note de comité (template FR/EN)

```
[EN-TÊTE]
Émetteur · Secteur · Notation composite (avant → après) · Priorité · Date · Langue

1. Événement
   Faits sourcés, phrase par phrase, avec citations [1], [2]…

2. Pourquoi c'est important pour le crédit
   Analyse sourcée : position par rapport à la frontière IG/HY, liquidité, guidance, émission.

3. Indicateurs clés
   Données sourcées (XBRL pour les émetteurs US, communiqué de résultats sinon).
   Une donnée absente est notée « Non disponible dans les sources ».

4. Points à vérifier par l'analyste
   Questions ouvertes, sans recommandation.

5. Sources
   Liste numérotée : titre, URL, date de publication, date de récupération, hash.

6. Vérification
   n claims VERIFIED · n PARTIAL · n exclus (UNSUPPORTED), avec annexe.

Mention : « Brouillon généré par IA, à valider par l'analyste. Aucune recommandation d'investissement. »
```

---

## 13. Bibliothèque de prompts

### 13.1 Liste MVP

| Fichier | Rôle | Langue |
|---|---|---|
| `extraction/rating_action.v1.yaml` | Extraire une action de notation en JSON | EN |
| `extraction/issuance.v1.yaml` | Extraire une émission (montant, devise, coupon, maturité, séniorité) | EN |
| `extraction/earnings_signals.v1.yaml` | Extraire guidance et signaux (liquidité, covenant, dépréciation) | EN |
| `notes/event_summary.fr.v1.yaml` / `.en` | Résumé court d'un événement | FR / EN |
| `notes/committee_note.fr.v1.yaml` / `.en` | Sections 1, 2 et 4 de la note | FR / EN |
| `verification/claim_support.v1.yaml` | Juger si un passage soutient un claim | EN |

### 13.2 Format

```yaml
id: extraction.rating_action
version: 1.0.0
language: en
purpose: Extract a single rating action from a source text.
model_role: extraction
temperature: 0
input_variables: [issuer_name, source_text]
output_schema: RatingActionExtraction    # schéma pydantic exporté en JSON Schema
system: |
  ...
user: |
  ...
changelog:
  - 1.0.0: initial version
```

### 13.3 Guide de style (`prompts/STYLE_GUIDE.md`)

- Factuel, sans spéculation ni recommandation (« acheter », « vendre », « surpondérer » interdits).
- Chaque phrase porte au moins une citation `[n]`.
- Les nombres sont repris exactement, avec unité et devise.
- Information absente : « Non disponible dans les sources » / « Not available in sources ».
- Dates absolues, jamais « hier » ni « la semaine dernière ».
- Glossaire FR/EN imposé : notation / rating ; perspective / outlook ; mise sous surveillance négative / negative watch (CreditWatch negative, review for downgrade) ; fallen angel ; rising star ; dette senior / senior debt ; dette subordonnée / subordinated debt ; écart de crédit / spread.

---

## 14. Journal d'audit

Chaque entrée de `audit_log` contient : `timestamp`, `step`, `event_id` ou `doc_id`, `inputs_hash`, `outputs_hash`, `model_id`, `prompt_id@version`, `rules_version`, `latency_ms`, `cost_usd`, `status`, `message`.
Chaque exécution d'évaluation enregistre aussi le hash du commit Git.

---

## 15. Protocole d'évaluation

### 15.1 Jeu gold

- MVP : 60 événements (environ 20 notation, 20 résultats, 20 émissions, dont au moins 12 P1) et 30 documents de bruit, c'est-à-dire non matériels ou hors sujet, pour mesurer les faux positifs.
- Cible Should : 100 événements et 40 documents de bruit.
- Période : 2025 à 2026, sur des émetteurs de la watchlist ou des émetteurs comparables.
- Format `eval/gold/events.jsonl` :

```json
{"gold_id": "G001", "doc_url": "...", "retrieved_at": "2026-10-08", "issuer_id": "...", "family": "rating", "event_type": "downgrade", "fields": {"agency": "SP", "old_rating": "BBB-", "new_rating": "BB+"}, "expected_priority": "P1", "expected_rules": ["RAT-02"], "notes": "..."}
```

### 15.2 Guide d'étiquetage

`eval/LABELING_GUIDE.md` est rédigé et figé **avant** la première exécution des modèles. Il définit les types d'événements, les champs, l'application de la matrice et les cas limites. Une modification après coup est versionnée et justifiée dans le rapport.

### 15.3 Métriques

- Détection d'événements : précision, rappel, F1.
- Classification du type d'événement : exactitude et matrice de confusion.
- Extraction des champs : exact match par champ (notations, montants, devises).
- Priorité : exactitude, matrice de confusion P1/P2/P3, et **Recall@P1 présenté en k/n avec intervalle de confiance à 95 % de Wilson**.
- Ancrage : part des claims `VERIFIED`, `PARTIAL`, `UNSUPPORTED`.
- Coût par événement et latence de traitement par étape.
- Run live uniquement : délai publication → alerte, fausses alertes par émetteur et par jour.

### 15.4 Comparaison de LLM

Même bibliothèque de prompts, température 0, mêmes documents.

| Modèle | Recall@P1 | Précision détection | Exact match champs | Claims VERIFIED | Latence médiane | Coût / 1000 événements |
|---|---|---|---|---|---|---|

Objectif : montrer une démarche de choix de solution, pas désigner le « meilleur » rédacteur.

### 15.5 Biais et limites à documenter

- Petit échantillon : intervalles de confiance larges.
- Biais d'étiquetage : annotateur unique, sauf double annotation partielle.
- Contamination : les LLM peuvent connaître certains événements passés grâce à leur entraînement. Les événements du run live servent de contrôle.

### 15.6 Règles de publication

- `eval/run_eval.py` génère `eval/reports/<run_id>.md` (commit, versions de prompts, modèles, date, métriques).
- Le tableau de résultats du README est copié automatiquement depuis le dernier rapport.

---

## 16. Livrables

### Livrable 1 · Prototype (repo GitHub dédié)

README en anglais avec résumé FR. Contenu : pitch en deux lignes, GIF de démo, diagramme d'architecture (Mermaid), quick start en moins de 5 minutes, exemple de panneau « Why this priority? », tableau de résultats auto-généré, limites, disclaimer.

### Livrable 2 · Vidéo de démo (2 minutes)

1. Le problème (15 s)
2. Une alerte P1 arrive, panneau « Why? » (30 s)
3. Clic sur une citation, passage source surligné (20 s)
4. Note de comité FR puis EN (25 s)
5. Résultats du benchmark (20 s)
6. Limites et prochaines étapes (10 s)

### Livrable 3 · Case study (4 à 5 pages + synthèse d'une page)

1. Problème : pourquoi la surveillance crédit est difficile
2. Contexte public R&Co : publication trimestrielle et offre de stage, paraphrasées et citées
3. Hypothèse de workflow actuel (explicitement une hypothèse)
4. Solution et architecture
5. Évaluation
6. Résultats
7. Limites
8. Passage du prototype au pilote

### Livrable 4 · Roadmap 180 jours (1 à 2 pages)

« From Prototype to Production · Proposed 180-Day Roadmap »

| Mois | Phase | Contenu |
|---|---|---|
| M1 | Discovery | Entretiens analystes et gérants, cartographie du workflow, points de friction, priorisation des cas d'usage |
| M2 | Prototype | 2 à 3 cas d'usage, benchmark historique, évaluation des LLM et des prestataires |
| M3 | Pilote | 2 à 3 analystes, intégration Teams, boucle de feedback |
| M4 | Intégration des données | Bloomberg et données internes, sécurité, journalisation |
| M5 | Extension | Toute l'équipe Fixed Income, monitoring, gestion des versions de prompts |
| M6 | Évaluation production | KPIs, adoption, ROI, prochains cas d'usage |

Mention : *Illustrative roadmap based solely on publicly available information; actual priorities would be defined with the team.*

---

## 17. Planning

| Jour | Objectif | Sortie |
|---|---|---|
| J1 | Repo, pyproject, config, schémas pydantic, `rating_scales.yaml`, notation composite + tests | Phase 1 |
| J2 | `universe.yaml` et `ratings_seed.csv` vérifiés à la main, brouillon du guide d'étiquetage | Seeds sourcés |
| J3 | Connecteur EDGAR (submissions, 8-K, FWP, XBRL), snapshots, hash | Phase 2 (US) |
| J4 | Connecteurs IR et news, déduplication, rattachement émetteur, SQLite, audit | Phase 2 complète |
| J5 | Couche LLM, cache, prompts d'extraction, validation des spans et des nombres | Phase 3 |
| J6 | Tests de `rules.yaml` puis moteur de matérialité, panneau « Why? » | Phase 4 |
| J7 | Contextualisation, vérification des claims, notes FR/EN | Phase 5 |
| J8 | Alertes locales et Teams, viewer Streamlit, **démarrage du run live** | Phase 6 |
| J9 | Jeu gold (60 + 30), harnais d'évaluation, comparaison de 2 LLM | Phase 7 |
| J10 | README, vidéo de démo, **candidature** | Candidature envoyée |
| J11-J14 | Extension à 100 événements, analyse d'erreurs, ajustements, module Capex IA vs FCF | Should |
| J15 | Fin du run live, métriques live | Rapport live |
| J16-J17 | Case study et roadmap 180 jours | Livrables 3 et 4 |
| J18 | Message LinkedIn ciblé avec démo, case study et roadmap | Relance |

Le guide d'étiquetage et la collecte des documents gold peuvent avancer en parallèle dès J2, à raison d'environ une heure par jour.

---

## 18. Risques et parades

| Risque | Parade |
|---|---|
| Accès aux communiqués des agences limité | Communiqués des émetteurs, news, EDGAR |
| Tags XBRL hétérogènes | Table de tags de repli par indicateur, valeur « Non disponible » sinon |
| Hallucinations | Extraction validée par spans, vérification des claims, exclusion des claims non soutenus |
| Fuite de données d'évaluation | Guide d'étiquetage figé, run live comme contrôle |
| Coût des appels LLM | Cache, modèle léger pour l'extraction, plafond de budget dans `.env` |
| Pas de tenant Teams | Rendu local systématique, webhook optionnel |
| Dérive du périmètre | Liste « Won't » et roadmap |
| Confusion avec une production Rothschild | Disclaimer, pas de branding, univers nommé « Demo watchlist » |

---

## 19. Définition de « terminé » (MVP)

- [ ] `uv run pytest` passe, avec tests sur chaque règle et chaque modificateur
- [ ] `radar ingest` puis `radar process` tournent de bout en bout sur la watchlist
- [ ] Au moins une alerte P1 réelle ou historique est rendue avec son panneau « Why? »
- [ ] Une note de comité FR et une EN sont générées, avec 100 % des claims publiés en `VERIFIED` ou `PARTIAL` signalé
- [ ] Le journal d'audit permet de retracer une alerte jusqu'aux documents sources et à leurs hash
- [ ] Le rapport d'évaluation est généré automatiquement avec Recall@P1 en k/n
- [ ] Le README contient le quick start, le diagramme, les résultats réels, les limites et le disclaimer
- [ ] Aucun logo ni élément de charte Rothschild & Co dans le repo
