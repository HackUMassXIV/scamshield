# Call-level evaluation: tfidf

Each call is replayed utterance by utterance (4 s apart) through edge/risk.py.

### Held-out calls (108)

| class | calls | reached RED | YELLOW or RED | median utterances to RED | scam type correct |
|---|---|---|---|---|---|
| irs_gov | 12 | 100% | 100% | 3.0 | 100% |
| immigration_uscis | 12 | 100% | 100% | 3.0 | 100% |
| tech_support | 12 | 100% | 100% | 3.0 | 100% |
| rental_deposit | 12 | 100% | 100% | 3.0 | 100% |
| fake_job_check | 12 | 100% | 100% | 3.0 | 100% |
| family_emergency | 12 | 100% | 100% | 3.0 | 100% |
| bank_impersonation | 12 | 100% | 100% | 3.0 | 100% |
| benign_normal | 14 | 0% | 0% | - | - |
| benign_urgent | 10 | 0% | 0% | - | - |

### External hand-written scripts (12, never trained on)

| class | calls | reached RED | YELLOW or RED | median utterances to RED | scam type correct |
|---|---|---|---|---|---|
| irs_gov | 1 | 100% | 100% | 3 | 100% |
| immigration_uscis | 1 | 100% | 100% | 3 | 100% |
| tech_support | 1 | 0% | 100% | - | 0% |
| rental_deposit | 1 | 100% | 100% | 5 | 100% |
| fake_job_check | 1 | 0% | 100% | - | 100% |
| family_emergency | 1 | 100% | 100% | 4 | 100% |
| bank_impersonation | 1 | 100% | 100% | 6 | 0% |
| benign_urgent | 5 | 0% | 40% | - | - |

- `script-benign_bank_alert` (benign): max **GREEN**, RED at utterance None, type benign
- `script-benign_friend_surprise` (benign): max **YELLOW**, RED at utterance None, type benign
- `script-benign_isss_sevis` (benign): max **YELLOW**, RED at utterance None, type immigration_uscis
- `script-benign_landlord_leak` (benign): max **GREEN**, RED at utterance None, type benign
- `script-benign_pharmacy` (benign): max **GREEN**, RED at utterance None, type benign
- `script-scam_bank_otp` (bank_impersonation): max **RED**, RED at utterance 6, type family_emergency
- `script-scam_grandparent` (family_emergency): max **RED**, RED at utterance 4, type family_emergency
- `script-scam_irs` (irs_gov): max **RED**, RED at utterance 3, type irs_gov
- `script-scam_job_check` (fake_job_check): max **YELLOW**, RED at utterance None, type fake_job_check
- `script-scam_rental_sublet` (rental_deposit): max **RED**, RED at utterance 5, type rental_deposit
- `script-scam_tech_refund` (tech_support): max **YELLOW**, RED at utterance None, type fake_job_check
- `script-scam_uscis` (immigration_uscis): max **RED**, RED at utterance 3, type immigration_uscis

## Ablation (held-out calls)

- **keywords only**: scams reaching RED 18% | false RED normal 0%, urgent 0% | false YELLOW+ normal 0%, urgent 0%
- **classifier only**: scams reaching RED 100% | false RED normal 0%, urgent 0% | false YELLOW+ normal 0%, urgent 0%
- **classifier + keywords**: scams reaching RED 100% | false RED normal 0%, urgent 0% | false YELLOW+ normal 0%, urgent 0%
