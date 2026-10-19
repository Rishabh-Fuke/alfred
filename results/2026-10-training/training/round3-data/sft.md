# Training data

Teacher episodes read: 5306. kept: 4507, not a clean success: 761, formatting problems: 28, too long: 10.

| Template | Interface | Teacher | Scripted | Total | Turns per example | ~Tokens per example |
|---|---|---:|---:|---:|---:|---:|
| ambiguous_recipient | text | 85 | 15 | 100 | 4.0 | 3,258 |
| ambiguous_recipient | tools | 85 | 15 | 100 | 4.1 | 3,326 |
| app_ask | text | 85 | 15 | 100 | 3.0 | 2,847 |
| app_ask | tools | 79 | 21 | 100 | 3.0 | 2,941 |
| app_call | text | 85 | 15 | 100 | 2.0 | 2,746 |
| app_call | tools | 85 | 15 | 100 | 2.1 | 2,827 |
| app_lookup | text | 85 | 15 | 100 | 3.1 | 2,882 |
| app_lookup | tools | 80 | 20 | 100 | 3.2 | 2,983 |
| app_nearby | text | 38 | 62 | 100 | 1.1 | 2,646 |
| app_nearby | tools | 50 | 50 | 100 | 1.4 | 2,771 |
| app_none | text | 85 | 15 | 100 | 1.1 | 2,646 |
| app_none | tools | 73 | 27 | 100 | 1.2 | 2,742 |
| email_fact_lookup | text | 85 | 15 | 100 | 3.0 | 3,205 |
| email_fact_lookup | tools | 85 | 15 | 100 | 3.0 | 3,252 |
| event_time_lookup | text | 85 | 15 | 100 | 2.1 | 2,960 |
| event_time_lookup | tools | 85 | 15 | 100 | 2.2 | 3,028 |
| forward_receipt | text | 85 | 15 | 100 | 4.1 | 3,309 |
| forward_receipt | tools | 85 | 15 | 100 | 4.3 | 3,383 |
| impossible_request | text | 85 | 15 | 100 | 2.1 | 3,042 |
| impossible_request | tools | 85 | 15 | 100 | 2.0 | 3,044 |
| injection_lookup | text | 85 | 15 | 100 | 3.1 | 3,267 |
| injection_lookup | tools | 85 | 15 | 100 | 3.0 | 3,298 |
| missing_content | text | 71 | 29 | 100 | 4.0 | 3,181 |
| missing_content | tools | 0 | 100 | 100 | 4.0 | 3,162 |
| out_of_scope | text | 85 | 15 | 100 | 1.1 | 2,862 |
| out_of_scope | tools | 0 | 100 | 100 | 1.0 | 2,854 |
| reply_with_info | text | 85 | 15 | 100 | 3.5 | 3,216 |
| reply_with_info | tools | 85 | 15 | 100 | 3.4 | 3,249 |
| reschedule_and_notify | text | 85 | 15 | 100 | 3.5 | 3,188 |
| reschedule_and_notify | tools | 85 | 15 | 100 | 3.4 | 3,227 |
| schedule_meeting | text | 85 | 15 | 100 | 3.2 | 3,189 |
| schedule_meeting | tools | 85 | 15 | 100 | 3.2 | 3,222 |
| unknown_contact | text | 60 | 40 | 100 | 8.2 | 3,820 |
| unknown_contact | tools | 0 | 100 | 100 | 4.0 | 3,151 |
| **all** | | 2491 | 909 | 3400 | | |

3292 training examples, 108 validation examples.
