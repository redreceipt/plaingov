# What drives engagement on X for @plaingov

Findings for #6 and #7, October 2026. Sources: X's published For You ranker ([xai-org/x-algorithm](https://github.com/xai-org/x-algorithm), `home-mixer/params/param.rs`, updated Sept 2026), X's [Automation Rules](https://help.x.com/en/rules-and-policies/x-automation), [developer guidelines](https://docs.x.com/developer-guidelines), and [API pricing](https://docs.x.com/x-api/getting-started/pricing). We have no engagement data of our own yet, so everything below is a hypothesis to measure. #10 adds the measuring.

## How X ranks a post

- Each post's score is a weighted sum of the *predicted* chance a viewer takes each action. Production weights: share via copy link 20, reply 5 (boosted between mutual follows), quote 5, share via DM 5, follow author 4, share 2, repost 1, like 0.66, click 0.3, open link 0.2. Negative: report −234, mute −59, not interested −52, block −31.
- **New posts from small accounts get a trial.** A post under 2 hours old with fewer than 200 impressions, from an account under 50k followers, is lifted into feed slots 15–16 for a sample of viewers. Early reactions decide whether it spreads. Posts older than 48 hours aren't shown in For You at all.
- Replies and reposts from accounts a viewer doesn't follow are filtered out, so a thread's later posts only reach our followers.
- No hashtag signal shows up anywhere in the ranker.

## What this means for us

| Question | Finding | Action |
|---|---|---|
| Single post vs thread | A thread's replies don't reach non-followers. | Keep single posts (#5). |
| Links | A link click is worth 0.2, about a third of a like. Posts with a link cost $0.20 vs $0.015. There's no explicit link penalty, but a link mostly costs money without earning ranking. | Test milestone posts without links (#12). |
| Images | Photo expand is worth 0.05. Any benefit would come from longer dwell and more shares. | Test a tracker card (#13). |
| Asking a question | Retired by editorial choice, not an experiment result. | No appended discussion prompts. Present concrete facts and let discussion happen organically (#44). |
| Timing | The first 2 hours decide reach, and the hourly timer posts around the clock, including overnight in the U.S. | Post only during U.S. daytime (#11). |
| Volume | 6 a day, spaced out, is fine. The ranker spaces out repeat authors on its own. | Keep it, and revisit with data. |
| Topics and stages | Unknown until we measure. jev already picks what's most interesting. | Break down the weekly report by stage and by state vs federal. |
| Hashtags, polls | No ranking signal for either. | Skip. |
| Tagging legislators (#4) | **Not allowed as automated.** The Automation Rules ban automated mentions unless the mentioned account opted in (for example, by replying to us or DMing us). X's guidelines list "scheduled content (news…)" accounts as allowed only with "no unsolicited mentions." Doing it anyway risks removal from search or suspension. | Name sponsors in plain text, without the @ (comment on #4). |
| Replying to news, quote posts | Automated replies based on keyword search are banned, and AI-generated replies need X's prior approval. | Skip. Do it by hand if at all. |
| Auto-following (#7) | **Not worth it.** Bulk, aggressive, or automatic following is banned. In the ranker, who follows *us* (reputation, SimClusters) drives distribution, not who we follow. Government offices rarely follow back. | Don't build it. |
| Measurement | Reading our own posts costs $0.001 each ("owned reads"): impressions, likes, reposts, replies, quotes, and bookmarks, plus profile and link clicks for posts under 30 days old. A week of 42 posts costs about $0.04. | Weekly report (#10). |

## Editorial direction

Lead with who is affected and the concrete change, preserving conditions and proposal/law status. No manufactured discussion questions, reaction prompts, or invented tradeoffs. The link experiment (#12) continues through November 7; #13 tracks the separate image trial. Historical question posts remain in reports so results are not relabeled.

## Also check

- @plaingov should have X's "Automated" account label (Settings → Your account → Account information → Automation), which the Automation Rules require for bot accounts.

## Not researched

What makes comparable accounts (GovTrack, civic explainers) succeed: reading their posts through the API costs money, and anonymous reads are rate-limited. Our own weekly report is the cheaper evidence.
