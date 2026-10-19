# Training data

Teacher episodes read: 4206. kept: 3692, not a clean success: 484, formatting problems: 20, too long: 10.

| Template | Interface | Teacher | Scripted | Total | Turns per example | ~Tokens per example |
|---|---|---:|---:|---:|---:|---:|
| ambiguous_recipient | text | 85 | 15 | 100 | 4.0 | 3,258 |
| ambiguous_recipient | tools | 85 | 15 | 100 | 4.1 | 3,326 |
| email_fact_lookup | text | 85 | 15 | 100 | 3.0 | 3,199 |
| email_fact_lookup | tools | 85 | 15 | 100 | 3.1 | 3,247 |
| event_time_lookup | text | 85 | 15 | 100 | 2.1 | 2,972 |
| event_time_lookup | tools | 85 | 15 | 100 | 2.2 | 3,031 |
| forward_receipt | text | 85 | 15 | 100 | 4.0 | 3,303 |
| forward_receipt | tools | 85 | 15 | 100 | 4.2 | 3,366 |
| impossible_request | text | 85 | 15 | 100 | 2.2 | 3,063 |
| impossible_request | tools | 85 | 15 | 100 | 2.0 | 3,053 |
| injection_lookup | text | 85 | 15 | 100 | 3.1 | 3,270 |
| injection_lookup | tools | 85 | 15 | 100 | 3.1 | 3,328 |
| missing_content | text | 71 | 29 | 100 | 4.0 | 3,182 |
| missing_content | tools | 0 | 100 | 100 | 4.0 | 3,162 |
| out_of_scope | text | 85 | 15 | 100 | 1.1 | 2,863 |
| out_of_scope | tools | 0 | 100 | 100 | 1.0 | 2,854 |
| reply_with_info | text | 85 | 15 | 100 | 3.6 | 3,231 |
| reply_with_info | tools | 85 | 15 | 100 | 3.5 | 3,257 |
| reschedule_and_notify | text | 85 | 15 | 100 | 3.5 | 3,194 |
| reschedule_and_notify | tools | 85 | 15 | 100 | 3.5 | 3,233 |
| schedule_meeting | text | 85 | 15 | 100 | 3.2 | 3,194 |
| schedule_meeting | tools | 85 | 15 | 100 | 3.2 | 3,222 |
| unknown_contact | text | 60 | 40 | 100 | 8.2 | 3,821 |
| unknown_contact | tools | 0 | 100 | 100 | 4.0 | 3,151 |
| **all** | | 1746 | 654 | 2400 | | |

2334 training examples, 66 validation examples.
