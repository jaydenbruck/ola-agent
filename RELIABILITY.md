# Ola reliability

Ola is a hackathon project by Jayden Bruck.

132 tests ran to a test outcome. 130 passed, 0 failed, 0 errors, 2 blocked and 1 skipped. 133 evidence rows in total.

Tested commit 4fcc52ff398e28555143a75ec8b9aafd0690ebb9. Window 2026-09-13T20:26:30+02:00 to 2026-09-13T20:31:18+02:00. Python 3.13.1 on Windows; real-model setting x-ai/grok-4.5.

## Method

The runner executes the entire server unit directory, then e2e, then live, using the same Python environment. Python rows come from pytest JUnit output, with parameter cases counted separately. Swift rows come from completed XCTest case lines checked against the suite total, preserving skips. WhatsApp checks exercise the browser wrapper against local pages. E2e checks use the real model when configured. Live checks require their account credentials. Blocked means an attempted journey did not establish its requested outcome; it is not a pass. A skip proves nothing about a live service. Portable Swift tests then run in an isolated build directory; the real route journey supplies their SSE fixture. Only the scoped live journeys and supplemental browser checks are included. This does not compile or test iOS rendering. Sanitized XML, command logs and the run manifest are stored under build/reliability. No count is a success-rate forecast.

## Real-site outcomes

| Site | Observed outcome | Evidence |
|---|---|---|
| kleinanzeigen | PASSED: listing 'Wir kaufen Ihr altes Fahrrad ,bar cash,sofort, K e i n e E-Bikes!' opened at https://www.kleinanzeigen.de/s-anzeige/wir-kaufen-ihr-altes-fahrrad-bar-cash-sofort-k-e-i-n-e-e-bikes-/499677045-217-1052, price 150 €. | server/tests/live/logs/kleinanzeigen-20260913-203046.md |
| lieferando | BLOCKED: A dialog remained open on the restaurant list; the cart was not reached. See the live log. | server/tests/live/logs/lieferando-20260913-203038.md |
| uber | BLOCKED: No fare was displayed after both route fields were set; the ride options required sign-in. See the live log. | server/tests/live/logs/uber-20260913-202944.md |

## Conversation and concurrent jobs

| What it proves | How it was run | Result |
|---|---|---|
| Turn event order | unit: test_agent | PASSED |
| Two jobs run in parallel | unit: test_agent | PASSED |
| Needs you then resume | unit: test_agent | PASSED |
| Resume from chat tool | unit: test_agent | PASSED |
| Cancel job | unit: test_agent | PASSED |
| Cut arguments are answered | unit: test_agent | PASSED |
| Unknown tool gets the real list | unit: test_agent | PASSED |
| Language and facts in prompt | unit: test_agent | PASSED |
| Job sees only job tools and gets the member language | unit: test_agent | PASSED |
| Failed job is spoken honestly | unit: test_agent | PASSED |
| Job without job tools fails honestly | unit: test_agent | PASSED |
| Cancel while waiting clears needs you | unit: test_agent | PASSED |
| Streams text and accumulates tool call fragments | unit: test_model | PASSED |
| Cut arguments are marked | unit: test_model | PASSED |
| Retries on 429 and 5xx then succeeds | unit: test_model | PASSED |
| Gives up after retries and reports 4xx plainly | unit: test_model | PASSED |
| Image parts and tool messages | unit: test_model | PASSED |
| Env defaults | unit: test_model | PASSED |
| The real model starts two jobs and speaks their controlled lookup results in German | e2e: test_turn_real_model | PASSED |
| The real model answers an English request using stored test memory | e2e: test_turn_real_model | PASSED |

## Reminders

| What it proves | How it was run | Result |
|---|---|---|
| Remember and remind | unit: test_agent | PASSED |
| The real model schedules a reminder that appears later in the conversation | e2e: test_turn_real_model | PASSED |

## Browser and takeover

| What it proves | How it was run | Result |
|---|---|---|
| Goto returns the page and a step sentence | unit: test_browser_actions | PASSED |
| Goto failure says why and does not raise | unit: test_browser_actions | PASSED |
| Dismiss dialog prefers reject | unit: test_browser_actions | PASSED |
| Dismiss dialog closes a modal without a reject control | unit: test_browser_actions | PASSED |
| Click the header anmelden not the footer partner link | unit: test_browser_actions | PASSED |
| Click a stale ref fails with fresh refs | unit: test_browser_actions | PASSED |
| Fill many fields at once | unit: test_browser_actions | PASSED |
| Pick completes a suggestion picker first time | unit: test_browser_actions | PASSED |
| Pick never takes the location or map rows | unit: test_browser_actions | PASSED |
| Pick on a plain field says so | unit: test_browser_actions | PASSED |
| Pick with no matching row lists what it offers | unit: test_browser_actions | PASSED |
| Press and scroll and read | unit: test_browser_actions | PASSED |
| Wait for waits for readiness not a fixed time | unit: test_browser_actions | PASSED |
| Wait for url | unit: test_browser_actions | PASSED |
| Upload puts the attachment into the file input | unit: test_browser_actions | PASSED |
| Needs you returns the reason and url | unit: test_browser_actions | PASSED |
| Unknown action is reported not raised | unit: test_browser_actions | PASSED |
| Close job closes the page | unit: test_browser_actions | PASSED |
| Extension export shape is normalized to playwright shape | unit: test_browser_cookies | PASSED |
| Same site none forces secure and placeholder cookies are dropped | unit: test_browser_cookies | PASSED |
| Seed accepts an extension export and the cookie lands | unit: test_browser_cookies | PASSED |
| One bad cookie does not sink a good one | unit: test_browser_cookies | PASSED |
| Seed log carries only counts and path never a value | unit: test_browser_cookies | PASSED |
| Absent seed file is a no op | unit: test_browser_cookies | PASSED |
| Bad seed file is skipped without raising | unit: test_browser_cookies | PASSED |
| No seed env is a no op | unit: test_browser_cookies | PASSED |
| Refs are stable across rereads | unit: test_browser_snapshot | PASSED |
| Refs never repeat within a job across pages | unit: test_browser_snapshot | PASSED |
| Regions put header before footer and dialog first | unit: test_browser_snapshot | PASSED |
| Footer is capped with a count | unit: test_browser_snapshot | PASSED |
| Picker fields are flagged and plain fields are not | unit: test_browser_snapshot | PASSED |
| Password page says use needs you and never shows a value | unit: test_browser_snapshot | PASSED |
| Sign up page carries the warning | unit: test_browser_snapshot | PASSED |
| Snapshot is short and read is long | unit: test_browser_snapshot | PASSED |
| Element cap holds on a busy page | unit: test_browser_snapshot | PASSED |
| Site facts reach the page head for known sites only | unit: test_browser_snapshot | PASSED |
| Tool schema names every action | unit: test_browser_snapshot | PASSED |
| Frame route serves the latest jpeg and checks the bearer | unit: test_browser_takeover | PASSED |
| Member signs in through input then after resume shows the page | unit: test_browser_takeover | PASSED |
| Sign in survives a browser restart on the same profile | unit: test_browser_takeover | PASSED |
| Frames are sharp and the model image small | unit: test_browser_takeover | PASSED |
| A desktop page keeps the frame shape and maps taps | unit: test_browser_takeover | PASSED |
| Expected site failure is blocked | unit: test_reliability | PASSED |
| Signin wall needs you takeover resume through the wire | e2e: test_agent_browser | PASSED |
| The real model hands over a local sign-in form and reads the balance after the test member resumes | e2e: test_browser_takeover | PASSED |
| Real HTTP and SSE carry two overlapping browser jobs, chat during takeover, sign-in input, resume and page results | live: test_agent_routes | PASSED |
| Attempts the requested Uber route; only a displayed fare passes | live: test_browser_sites | BLOCKED: No fare was displayed after both route fields were set; the ride options required sign-in. See the live log. |
| Lieferando margherita to the cart | live: test_browser_sites | BLOCKED: A dialog remained open on the restaurant list; the cart was not reached. See the live log. |
| Supplemental browser check: opens a Kleinanzeigen listing and reads its price | live: test_browser_sites | PASSED |

## Server, memory and tool contracts

| What it proves | How it was run | Result |
|---|---|---|
| Seq per thread and history | unit: test_events | PASSED |
| Close all ends every stream | unit: test_events | PASSED |
| Sse framing | unit: test_events | PASSED |
| Stream replays then goes live with keepalive | unit: test_events | PASSED |
| Bearer [redacted] every route | unit: test_main | PASSED |
| Chat events jobs resume | unit: test_main | PASSED |
| Server stops without waiting on streams | unit: test_main | PASSED |
| Cancel and attachments | unit: test_main | PASSED |
| Keeps passes, failures, skips and setup errors distinct | unit: test_reliability | PASSED |
| Swift skip cannot count as pass | unit: test_reliability | PASSED |
| Incomplete swift results are error | unit: test_reliability | PASSED |
| Removes configured secrets and access tokens from saved evidence | unit: test_reliability | PASSED |
| Preserves explicit environment settings when loading a file | unit: test_reliability | PASSED |
| A failed or empty test collection is an error. Case: 2 | unit: test_reliability | PASSED |
| A failed or empty test collection is an error. Case: 3 | unit: test_reliability | PASSED |
| A failed or empty test collection is an error. Case: 4 | unit: test_reliability | PASSED |
| A failed or empty test collection is an error. Case: 5 | unit: test_reliability | PASSED |
| An unfinished suite is an error | unit: test_reliability | PASSED |
| Nonzero exit cannot be hidden by passing xml | unit: test_reliability | PASSED |
| Register module openai and bare shapes | unit: test_tools_memory_prompt | PASSED |
| Load optional skips missing modules | unit: test_tools_memory_prompt | PASSED |
| Memory roundtrip | unit: test_tools_memory_prompt | PASSED |
| Example facts file is valid | unit: test_tools_memory_prompt | PASSED |
| Prompts carry voice rules and facts | unit: test_tools_memory_prompt | PASSED |
| Language detection | unit: test_tools_memory_prompt | PASSED |
| Parse when | unit: test_tools_memory_prompt | PASSED |

## WhatsApp

| What it proves | How it was run | Result |
|---|---|---|
| Sends the exact text once and confirms a new message ID with a send receipt | unit: test_whatsapp | PASSED |
| Reads loaded chat messages without sending anything | unit: test_whatsapp | PASSED |
| Reads the visible chat list without sending anything | unit: test_whatsapp | PASSED |
| Requests QR takeover before trying to open or send a chat | unit: test_whatsapp | PASSED |
| Whatsapp hidden chat list does not mask qr | unit: test_whatsapp | PASSED |
| Continues on the same page after linking | unit: test_whatsapp | PASSED |
| Does not send when two contacts have the same name | unit: test_whatsapp | PASSED |
| Does not send to a missing contact | unit: test_whatsapp | PASSED |
| Does not retry a send without a new receipt. Case: queued | unit: test_whatsapp | PASSED |
| Does not retry a send without a new receipt. Case: no-new-message | unit: test_whatsapp | PASSED |
| Stops if the selected recipient changes before sending | unit: test_whatsapp | PASSED |
| Does not guess when the send button is missing | unit: test_whatsapp | PASSED |
| An unfinished page load is not mistaken for a linked account | unit: test_whatsapp | PASSED |
| Rejects an empty contact or message before opening the browser. Case: -Hello | unit: test_whatsapp | PASSED |
| Rejects an empty contact or message before opening the browser. Case: None-Hello | unit: test_whatsapp | PASSED |
| Rejects an empty contact or message before opening the browser. Case: Alex- | unit: test_whatsapp | PASSED |
| Rejects an empty contact or message before opening the browser. Case: Alex-None | unit: test_whatsapp | PASSED |
| Rejects an empty contact or message before opening the browser. Case: Alex-12 | unit: test_whatsapp | PASSED |
| Each reported action has a page image captured after it | unit: test_whatsapp | PASSED |
| Whatsapp registry handler emits frames | unit: test_whatsapp | PASSED |
| Whatsapp concurrent sends keep their recipient | unit: test_whatsapp | PASSED |
| The real WhatsApp QR page provides a takeover request and image | live: test_whatsapp_live | PASSED |
| A real send receipt and matching message ID confirm the chat round trip | live: test_whatsapp_live | SKIPPED: Missing OLA_WHATSAPP_TEST_CONTACT, an authorized test recipient |

## iPhone event parsing and state

| What it proves | How it was run | Result |
|---|---|---|
| Blank Events Comments And Incomplete Disconnect | swift: CoreTests | PASSED |
| Byte Boundaries And Multiline | swift: CoreTests | PASSED |
| CRAnd Persistent IDAnd Empty Data | swift: CoreTests | PASSED |
| Captured Real Server Events | swift: CoreTests | PASSED |
| Completion Repairs Missing Deltas | swift: CoreTests | PASSED |
| Concurrent Replies Complete Once | swift: CoreTests | PASSED |
| Every Card Transition And No Duplicate | swift: CoreTests | PASSED |
| Failure And Reordered Card | swift: CoreTests | PASSED |
| Fitted Frame Rejects Letterbox | swift: CoreTests | PASSED |
| Public Door Prefix On Every Route | swift: CoreTests | PASSED |
| Rejected Input Cannot Count As An Accepted Action | swift: CoreTests | PASSED |
| Shutdown Comment Requests Reconnect Without Changing Cursor | swift: CoreTests | PASSED |
| Snapshot And Persistence | swift: CoreTests | PASSED |

## What failed and what we changed

The first real WhatsApp visit showed a browser compatibility screen with Playwright's default headless identity. Using a desktop Chrome identity reached the real QR linking page. The browser lane was given that finding so linking and takeover use the same persistent session. No message was sent in that probe. The first local WhatsApp run had 17 passes and one fixture failure: resetting a page redeclared its JavaScript variables. We scoped the fixture script to each page load, then all 18 WhatsApp checks passed. A later run hit two browser setup errors, including a Playwright driver allocation failure on this shared PC. The test fixture now reuses one Chromium process with a fresh context for each test. An encoding error in a German assertion was corrected; all 20 WhatsApp checks then passed. Swift's parallel xUnit output counted a skipped fixture test as a pass, so the runner now reads serial XCTest outcomes and checks their count against the suite summary. The first combined run on ed14410 recorded 117 passes and two failures. A due reminder spoke before its slow acknowledgement; the core now waits for that acknowledgement before delivering the reminder. WhatsApp showed an IndexedDB error under the deeply nested Windows pytest profile path. The same QR check passed with a short isolated workspace profile, which the live test now uses. The founder removed email and Calendar from scope; their tests are excluded from this report.

This report's run has no failed test outcomes or collection errors.

## Known limits

WhatsApp needs one QR linking step and an explicitly configured test contact for a live send; a QR screen is not a sent message.  
WhatsApp, Lieferando and Uber are the scoped apps. Sign-in walls and blocked pages do not prove completed errands.  
This is one run of Ola, a hackathon project by Jayden Bruck. It does not prove phone acceptance or production reliability.  
