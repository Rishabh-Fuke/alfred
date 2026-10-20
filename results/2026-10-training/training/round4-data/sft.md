# Training data

Teacher episodes read: 5956. kept: 4802, not a clean success: 1114, formatting problems: 30, too long: 10.

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
| app_multi | text | 71 | 29 | 100 | 4.9 | 3,037 |
| app_nearby | text | 38 | 62 | 100 | 1.1 | 2,647 |
| app_nearby | tools | 50 | 50 | 100 | 1.4 | 2,771 |
| app_none | text | 85 | 15 | 100 | 1.1 | 2,652 |
| app_none | tools | 73 | 27 | 100 | 1.2 | 2,741 |
| app_pick | text | 63 | 37 | 100 | 4.0 | 2,983 |
| app_pick | tools | 58 | 42 | 100 | 4.0 | 3,090 |
| app_require | text | 58 | 42 | 100 | 3.0 | 2,841 |
| app_require | tools | 45 | 55 | 100 | 3.0 | 2,936 |
| email_fact_lookup | text | 85 | 15 | 100 | 3.0 | 3,197 |
| email_fact_lookup | tools | 85 | 15 | 100 | 3.0 | 3,240 |
| event_time_lookup | text | 85 | 15 | 100 | 2.1 | 2,970 |
| event_time_lookup | tools | 85 | 15 | 100 | 2.3 | 3,035 |
| forward_receipt | text | 85 | 15 | 100 | 4.0 | 3,305 |
| forward_receipt | tools | 85 | 15 | 100 | 4.2 | 3,370 |
| impossible_request | text | 85 | 15 | 100 | 2.3 | 3,081 |
| impossible_request | tools | 85 | 15 | 100 | 2.0 | 3,048 |
| injection_lookup | text | 85 | 15 | 100 | 3.1 | 3,269 |
| injection_lookup | tools | 85 | 15 | 100 | 3.1 | 3,307 |
| missing_content | text | 71 | 29 | 100 | 4.0 | 3,181 |
| missing_content | tools | 0 | 100 | 100 | 4.0 | 3,162 |
| out_of_scope | text | 85 | 15 | 100 | 1.1 | 2,867 |
| out_of_scope | tools | 0 | 100 | 100 | 1.0 | 2,854 |
| reply_with_info | text | 85 | 15 | 100 | 3.5 | 3,217 |
| reply_with_info | tools | 85 | 15 | 100 | 3.4 | 3,258 |
| reschedule_and_notify | text | 85 | 15 | 100 | 3.6 | 3,204 |
| reschedule_and_notify | tools | 85 | 15 | 100 | 3.4 | 3,221 |
| schedule_meeting | text | 85 | 15 | 100 | 3.2 | 3,193 |
| schedule_meeting | tools | 85 | 15 | 100 | 3.2 | 3,218 |
| unknown_contact | text | 60 | 40 | 100 | 8.2 | 3,820 |
| unknown_contact | tools | 0 | 100 | 100 | 4.0 | 3,151 |
| **all** | | 2786 | 1114 | 3900 | | |

3780 training examples, 120 validation examples.
