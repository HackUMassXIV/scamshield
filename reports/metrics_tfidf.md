# Classifier report: tfidf

- model: tfidf(word1-2 + char3-5) + logistic regression
- train_windows: 30750
- test_windows: 5424
- train_seconds: 4.8
- ms_per_window: 0.049

| variant | n | acc | macro F1 | ECE | scam recall | benign FPR | benign-urgent FPR |
|---|---|---|---|---|---|---|---|
| all | 5424 | 0.863 | 0.850 | 0.038 | 0.973 | 0.000 | 0.000 |
| clean | 2712 | 0.865 | 0.853 | 0.036 | 0.973 | 0.000 | 0.000 |
| noisy | 2712 | 0.861 | 0.847 | 0.041 | 0.973 | 0.000 | 0.000 |

## Per-label F1 (all test windows)

| label | precision | recall | F1 | support |
|---|---|---|---|---|
| irs_gov | 0.801 | 0.809 | 0.805 | 648 |
| immigration_uscis | 0.865 | 0.853 | 0.859 | 510 |
| tech_support | 0.873 | 0.823 | 0.847 | 558 |
| rental_deposit | 0.858 | 0.788 | 0.821 | 660 |
| fake_job_check | 0.856 | 0.819 | 0.837 | 624 |
| family_emergency | 0.829 | 0.841 | 0.835 | 624 |
| bank_impersonation | 0.842 | 0.830 | 0.836 | 546 |
| benign | 0.917 | 1.000 | 0.957 | 1254 |

## Confusion matrix (rows = true, cols = predicted)

| | irs_gov | immigratio | tech_suppo | rental_dep | fake_job_c | family_eme | bank_imper | benign |
|---|---|---|---|---|---|---|---|---|
| **irs_gov** | 524 | 10 | 9 | 22 | 23 | 22 | 19 | 19 |
| **immigratio** | 22 | 435 | 8 | 4 | 9 | 11 | 7 | 14 |
| **tech_suppo** | 19 | 4 | 459 | 18 | 15 | 18 | 5 | 20 |
| **rental_dep** | 21 | 21 | 18 | 520 | 15 | 28 | 15 | 22 |
| **fake_job_c** | 19 | 10 | 7 | 21 | 511 | 17 | 17 | 22 |
| **family_eme** | 25 | 4 | 10 | 12 | 16 | 525 | 22 | 10 |
| **bank_imper** | 24 | 19 | 15 | 9 | 8 | 12 | 453 | 6 |
| **benign** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1254 |
