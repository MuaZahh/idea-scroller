Now I have enough information. Let me compile the analysis:

```json
{
  "gaps": [
    {
      "title": "Adaptive Fitness for Disabled & Limited Mobility Users",
      "description": "Across Fitbit, Home Workout, and Samsung Health reviews, disabled users report no support for non-standard exercises (e.g., wheelchair workouts, seated pilates, gentle exercises for those with crutches/walkers). One Fitbit user explicitly states: 'I don't want to do wheelchair exercises because I'm not in a wheelchair. I'm a disabled person that can stand & walk with crutches but I'd like to do gentle exercises like pilates or chair yoga. I can't do the HITT or the Peloton that everyone is excited about.' This is a real pain point across multiple apps with NO dedicated inclusive fitness solution.",
      "apps_mentioning": ["Fitbit", "Home Workout", "Samsung Health"],
      "sample_quotes": [
        "I'm a disabled person that can stand & walk with crutches but I'd like to do gentle exercises like pilates or chair yoga. I can't do the HITT or the Peloton that everyone is excited about.",
        "doesn't consider anyone with disabilities",
        "This app has no rest days, you cannot workout for 31 days straight (not realistic for disabled users)"
      ],
      "searches_performed": [
        "adaptive fitness app disabled wheelchair mobility exercises",
        "accessible workout app mobility impaired inclusive fitness"
      ],
      "competitors_found": [
        "Wheel Fit - wheelchair-focused fitness app (exists but limited scope)",
        "Accessercise - app for people with disabilities (exists but niche/small)",
        "Accessible Fitness - apps exist but are few and underserved"
      ],
      "market": "OPEN",
      "verdict": "VIABLE - Massive underserved market. Disabled/mobility-limited populations (wheelchair users, crutch users, chronic pain sufferers, post-injury recovery) are completely ignored by mainstream apps. An adaptive fitness app focused on SCALED exercise variations (not just 'wheelchair exercises' but real flexibility for different mobility levels) could serve millions. Market gap is real and growing.",
      "standalone_viable": true
    },
    {
      "title": "Menopause & Perimenopause Fitness/Health Tracking",
      "description": "Google Fit received 1-star review: 'Why does this app only cater for young women? Young refers to those who track their period. NO information exists for later stages in a woman's life, ie, peri menopausal, menopausal or post menopausal. I was shocked to discover that an app promoting itself as being relatively advanced seemed to forget about this.' This is NOT just a feature request—it's a completely absent use case. Menopause fitness tracking requires different metrics (hot flashes, sleep quality, bone health, hormone-related water retention, mood) than standard fitness apps offer.",
      "apps_mentioning": ["Google Fit"],
      "sample_quotes": [
        "Why does this app only cater for young women? NO information exists for later stages in a woman's life, ie, peri menopausal, menopausal or post menopausal.",
        "Incredibly disappointed!!"
      ],
      "searches_performed": [
        "menopause perimenopause fitness app tracking women health"
      ],
      "competitors_found": [
        "Menovation - menopause fitness app (exists but early stage)",
        "Reverse Health, Motion App - perimenopause apps (exist but focused on symptom tracking, not integrated fitness)"
      ],
      "market": "GROWING",
      "verdict": "VIABLE but CROWDED - Several apps exist (Menovation, Reverse), but reviews suggest they're symptom-tracking focused, not fully integrated fitness + health + cycle + workout adaptation. Market is opening up, but an app that deeply integrates menopause-aware fitness coaching, adaptive workouts, AND health metrics (bone density proxies, thermal comfort tracking) would compete well. Not completely open but still underserved.",
      "standalone_viable": true
    },
    {
      "title": "Food Database Accuracy & Nutrition Logging Ease",
      "description": "MyFitnessPal received multiple 1-2 star reviews about data quality: 'Even basic foods like cooked chicken have wildly inconsistent macros depending on the entry, making proper tracking almost impossible.' Another: 'LOTS of items have erroneous nutritional info even when it says 'verified' (green check mark).' Combined with complaints about UI complexity ('Everything takes more steps than it used to'), the gap is CLEAR: users want a simple, accurate food logger. The barcode scanner paywall exacerbates this (now premium-only after being free). This is NOT a feature request—it's a core functionality gap in every major app.",
      "apps_mentioning": ["MyFitnessPal", "Fitbit"],
      "sample_quotes": [
        "Even basic foods like cooked chicken have wildly inconsistent macros depending on the entry, making proper tracking almost impossible.",
        "LOTS of items have erroneous nutritional info even when it says 'verified' (green check mark).",
        "Barcode scanner is no longer free to use",
        "I want to be able to simply add a product and calorie count manually without searching a huge database."
      ],
      "searches_performed": [
        "food diary nutrition logging app ease of use manual entry",
        "\"nutrition tracking\" app \"food database\" accuracy verified entries"
      ],
      "competitors_found": [
        "MacroFactor - macro tracking app (exists, focused on macros)",
        "Cronometer - detailed nutrition app (exists but complex)",
        "My Food Diary - simpler alternative (exists but smaller)",
        "Nutrola - newer food tracking app (exists)"
      ],
      "market": "CROWDED",
      "verdict": "NOT VIABLE as standalone - Market is saturated with food logging apps. However, there's a niche for 'simple + accurate' (vs. comprehensive but complex). The real opportunity isn't food logging alone, but food logging + meal planning + shopping list integration with verified nutrition data. Competitors exist but none are dominating on accuracy + simplicity combo. Would need a strong differentiation (e.g., community-sourced accurate entries, AI-verified portions) to stand out.",
      "standalone_viable": false
    },
    {
      "title": "Step Counter Accuracy & Calibration",
      "description": "Across Google Fit (38 mentions), Samsung Health (30+ mentions), and Fitbit (15+ mentions), step counting is FUNDAMENTALLY broken. Google Fit: '14 miles when I walked 10 miles,' 'calculates steps 24 hours every day, which is practically impossible,' 'randomly does not count steps. Huge gaps in history.' Samsung Health: 'counted about a quarter of my steps,' 'not counting steps properly.' This is NOT a bug fix issue—it's a systematic algorithmic problem. Users explicitly state these apps are UNUSABLE for step tracking, the ONE core function. No app is solving this credibly.",
      "apps_mentioning": ["Google Fit", "Samsung Health", "Fitbit", "MyFitnessPal", "Step Counter"],
      "sample_quotes": [
        "Not tracking walk properly. It is calculating steps 24 hours every day, which is practically impossible.",
        "I walked around 10 miles today..app says 14 miles",
        "Randomly does not count steps. Huge gaps in the history",
        "counted about a quarter of my steps",
        "Step counter is not working",
        "I walked over 9 thousand steps on one particular day and the app recorded 3 thousand only"
      ],
      "searches_performed": [
        "step counter accuracy algorithm calibration walking tracking"
      ],
      "competitors_found": [
        "Google Fit - native Android (broken per reviews)",
        "Samsung Health - native Samsung (broken per reviews)",
        "Fitbit - wearable sync (broken per reviews)",
        "Step Counter app - free step counter (has accuracy issues per reviews)",
        "Most smartwatches have native step counting (issues noted across devices)"
      ],
      "market": "OPEN",
      "verdict": "VIABLE - This is a REAL GAP. The problem isn't that step counters don't exist; it's that they're ALL inaccurate. A step counting engine that actually works reliably (even if it's less fancy) would fill a genuine need. The opportunity: build a lightweight, SDK-quality step counter that can be embedded in other apps OR sold as a premium accuracy layer. Users are explicitly leaving apps because step counting doesn't work. The market is desperate for a RELIABLE solution.",
      "standalone_viable": true
    },
    {
      "title": "Data Backup & Loss Prevention for Workouts",
      "description": "Across MyFitnessPal, Fitbit, Nike Run Club, and Hevy, users report data mysteriously vanishing: MyFitnessPal: 'lost all my DB after app reinstall,' 'app keeps deleting workouts after I have entered them,' 'After I took a look at public preview... my calories in stopped showing.' Fitbit: 'Steps keep getting updated at random intervals. I completed 10K steps and then after an hour they got reset to 8500.' Nike Run Club: 'finished a run on my 10k plan today and went to look at the next run and now the plans are gone???' This is NOT just a sync issue—it's systematic data integrity failure. Users have NO way to recover lost data.",
      "apps_mentioning": ["MyFitnessPal", "Fitbit", "Nike Run Club", "Hevy", "Google Fit"],
      "sample_quotes": [
        "lost all my DB after app reinstall",
        "app keeps deleting workouts after I have entered them. I don't realise until a while later when it is too late to re-add them.",
        "I lost all my data. Thanks. The application is unstable.",
        "After I took a look at public preview and HATED IT... my calories in stopped showing",
        "Steps keep getting updated at random intervals... reset to 8500",
        "finished a run on my 10k plan today and went to look at the next run and now the plans are gone???"
      ],
      "searches_performed": [
        "data integrity backup fitness app automatic loss recovery",
        "workout data loss prevention app cloud backup automatic sync"
      ],
      "competitors_found": [
        "FitnessSyncer - cross-app sync tool (exists but third-party workaround)",
        "HealthSync - data sync tool (exists but niche)",
        "Most apps have cloud sync built-in (but it's failing per reviews)"
      ],
      "market": "OPEN",
      "verdict": "VIABLE - Not a standalone app, but a CRITICAL infrastructure gap. This would work best as: (1) A data recovery service for existing apps, OR (2) A sync middleware layer ensuring automatic backups to cloud. The pain point is real: users are losing MONTHS of workout data with zero recovery path. A 'Workout Data Safe' service that guarantees recovery of lost training logs would have immediate market traction. Users explicitly want this.",
      "standalone_viable": true
    },
    {
      "title": "Transparent Pricing & Paywall Before Sign-Up",
      "description": "Fitbod, AllTrails, Home Workout, Fitbod, and Step Counter all use dark pattern paywalls: Fitbod: 'Doesn't communicate that you need to sign up for a free trial or subscription before you can use anything,' 'They ask for your data before telling you it costs a subscription.' AllTrails: 'tell me before I download that there is a damned paywall.' Home Workout: 'After all the questions, it becomes clear why... they will charge you money.' Step Counter: 'Wants my credit card information for a \"free\" app.' This is systemic across apps—users are TRICKED into providing personal data only to hit a paywall. This generates negative reviews and refund requests but doesn't prevent it.",
      "apps_mentioning": ["Fitbod", "AllTrails", "Home Workout", "Step Counter", "Strava", "Fitbit"],
      "sample_quotes": [
        "Doesn't communicate that you need to sign up for a free trial or subscription before you can use anything in the app at all.",
        "After you send all your details you find out it's a paid app",
        "trash app baits you with 500 question THEN it tells you that you have to pay",
        "Be prepared for interruptions to upgrade, provide feedback, etc.",
        "tell me before I download that there is a damned paywall"
      ],
      "searches_performed": [
        "fitness app onboarding paywall free trial transparent pricing"
      ],
      "competitors_found": [
        "Standard practice - most apps hide pricing (not a solved problem)"
      ],
      "market": "CROWDED",
      "verdict": "NOT VIABLE as standalone app - This is a BUSINESS PRACTICE issue, not a product gap. Could be a B2B SaaS tool for app developers on transparent paywall design, but that's outside fitness. The real issue: dark patterns generate negative reviews but don't prevent downloads. This is a problem with app store policies, not a gap for a fitness app to fill.",
      "standalone_viable": false
    },
    {
      "title": "Cross-Platform Wearable Syncing (Apple Watch + Wear OS + Garmin)",
      "description": "Across Fitbit, Samsung Health, Strava, Garmin Connect, and others, multi-device syncing is broken: Samsung Health: 'Garmin watch data not syncing,' 'I have a different app for food tracking... this app is very limited in what information it will share with other health apps. It will only share daily steps.' Strava: 'Installed both on my samsung phone and watch... samsung health auto starts but strava does not get the data even when integration has been set.' Fitbit: 'I have to reconnect the Google Fit apps for the step tracking every time.' Garmin: 'does not synch with strava... tried multiple times.' The problem: each wearable/app ecosystem is siloed. Users with multi-brand ecosystems (Apple + Garmin, Samsung + Strava) are STUCK.",
      "apps_mentioning": ["Fitbit", "Samsung Health", "Strava", "Garmin Connect", "MyFitnessPal"],
      "sample_quotes": [
        "Installed both on my samsung phone and watch... samsung health auto starts and tracks my walks/runs, but strava does not get the data even when integration has been set",
        "does not synch with strava. tried multiple times. no support.",
        "I have to reconnect the Google Fit apps for the step tracking every time I open the app",
        "This app will only share daily steps. If this is the best samsung can do for a health and fitness app for their watches and trackers I won't be wasting my money on them again"
      ],
      "searches_performed": [
        "health connect sync standard Android fitness data interoperability",
        "smart watch fitness app cross-device syncing issues",
        "Apple Watch Wear OS cross-platform fitness app compatibility"
      ],
      "competitors_found": [
        "Health Connect (Google) - standard, but adoption is incomplete",
        "FitnessSyncer - third-party sync tool (exists but not seamless)",
        "Each app implements partial integrations (fragmented)"
      ],
      "market": "OPEN",
      "verdict": "VIABLE - This is a PLATFORM PROBLEM that could be solved by a middleware app. A universal wearable sync manager (pulls from Apple Health, Health Connect, Garmin API, Strava API, etc., and unifies into one source of truth) would be EXTREMELY valuable. Users with multiple wearables are a growing segment. This is NOT just a feature request—it's a structural gap in the ecosystem. A 'Universal Wearable Hub' app could succeed here.",
      "standalone_viable": true
    },
    {
      "title": "Real-Time Step Counter Display & Update Frequency",
      "description": "Samsung Health degraded this feature: users report 'Step counter no longer updates in real time,' 'freezes the count on the display for 2 minutes,' 'can't get live step count anymore.' Specifically: 'Used to be a good app. One cool thing it had was live step counter updates, even Google Fit and Apple don't offer. But with the latest update, I'm not seeing those live updates anymore.' This is a LOSS OF FEATURE, not a missing one, but the gap is clear: users WANT real-time step feedback while walking, and NO major app delivers this well anymore.",
      "apps_mentioning": ["Samsung Health", "Google Fit", "Fitbit"],
      "sample_quotes": [
        "After the oneui 8 update... the health app is showing the daily activity history as empty",
        "Step counter continues to count steps but freezes the count on the display for 2 minutes",
        "Live step count is a required part of any step counter for me and many other users switching to other apps",
        