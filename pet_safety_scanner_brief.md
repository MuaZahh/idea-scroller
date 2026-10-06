# Pet Safety Scanner — App Build Brief

## What This App Does

Point your camera at anything in your house — a plant, a food, a household product — and instantly find out if it's dangerous for your pet. That's it.

**Core flow:** Open app → Point camera → Get instant SAFE / TOXIC / DANGEROUS rating for your specific pet → If toxic: symptoms + what to do.

This is NOT a plant identifier that happens to mention toxicity. It's a **pet safety scanner** that happens to identify things. The user intent is panic-driven ("will this kill my cat?"), not curiosity-driven ("what plant is this?").

---

## Competitive Landscape


| Competitor       | Installs     | Rating | Weakness                                                                   |
| ---------------- | ------------ | ------ | -------------------------------------------------------------------------- |
| ToxiPets         | 10K          | 3.7/5  | Camera recognition "needs improving", only 44 reviews                      |
| Pet Protect Plan | Small        | Mixed  | Basic, only 720 species, $20/yr                                            |
| PictureThis      | 100M         | 3.5/5  | General plant app — pet toxicity is a buried feature behind $40/yr paywall |
| ASPCA            | Website only | N/A    | No camera, text lookup only                                                |


**There is no dominant player in pet safety scanning.** ToxiPets is the only dedicated app and it has 10K installs. Many high-value search keywords return generic plant apps or irrelevant results.

### ASO Keywords to Target

**App title should hit:** "pet plant", "plant scanner", "poison checker", "cats", "dogs"

Suggested name: **Pet Plant Scanner - Poison Checker for Cats & Dogs** (or similar)

**Keywords for description/metadata:**

- pet poison, poison scanner, toxic checker
- plant toxicity, plant poison
- pet food scanner, dog food checker, cat food checker
- pet safe plant checker, plant identifier for pets
- houseplant toxic cats, pet safety app

**Keywords where no pet-focused app currently ranks #1 (immediate opportunity):**

- "pet safe plant identifier" → PictureThis (generic)
- "plant identifier for pets" → PlantNet (generic)  
- "pet safe plant checker" → PlantSnap (generic)
- "dog safe plants" → irrelevant result
- "cat safe plants" → irrelevant result
- "plant toxicity" → app with 50 installs

---

## Business Model

**Monetization:** Subscription via App Store / Google Play billing. No auth system needed — no login, no email, no Google/Apple sign-in. The subscription is tied to the store account.

**Pricing (based on competitor research):**

- 7-day free trial (no credit card upfront if possible)
- ~$4.99/month $2.99*12/year

**Free tier:** Plant/food/product identification + SAFE/TOXIC rating  
**Premium tier:** LLM-powered follow-up conversation ("my cat just ate this leaf, she's 3 years old, should I go to the vet?"), detailed care advice, emergency steps, personalized to their pet's name/age/species.

**Revenue tooling:** Use RevenueCat or native StoreKit (iOS) / Google Play Billing (Android) for subscription management.

---

## Onboarding Flow

Based on what works for CoinSnap ($1.4M/month) and PictureThis ($5M/month):

1. **"What pet do you have?"** — Cat / Dog / Both / Other
2. **"What's their name?"** — Personalization hook (results say "Safe for Luna" not "Safe for cats")
3. **"How old are they?"** — Puppy/kitten vs adult (affects toxicity severity)
4. Trial - show them how it works or maybe show them a video of it working
5. **PAYWALL** — 7-day free trial, annual subscription. List benefits: unlimited scans, personalized to your pet
6. **Camera opens** — First scan is immediate. Show the magic moment fast.

**Key principles:**

- Paywall appears within 30-45 seconds of opening the app (this is industry standard)
- The 2-3 personal questions before the paywall create sunk cost + enable personalization
- No account creation. No login. Zero friction. Device-based identity.
- Optional email signup offered later for cross-device sync

---

## Tech Stack

### Architecture

Camera → Image → Vision LLM API → Structured response → Display result

### Backend / AI

- **LLM Vision API:** Claude Haiku (cheapest, fast, good enough for plant/food ID)
  - Send base64 image + system prompt
  - Returns: identification, toxicity level, symptoms, actions
  - Cost: fractions of a cent per scan
- **System prompt** should include the pet's species, name, age for personalized responses
- **Premium feature:** Multi-turn conversation with the LLM ("what if she only licked it?", "should I induce vomiting?", "nearest emergency vet?")

### System Prompt (tested and working)

```
You are a pet safety expert. When shown a photo:

1. IDENTIFY the item (plant, food, or household product)
2. PET TOXICITY: Rate for {pet_species}: SAFE / MILDLY TOXIC / TOXIC / HIGHLY TOXIC
3. SYMPTOMS: What happens if {pet_name} ingests this?
4. WHAT TO DO: Immediate steps
5. CONFIDENCE: How confident are you? (0-100%)

Personalize all responses for {pet_name}, a {pet_age} year old {pet_species}.

Respond in JSON:
{
  "item_name": "Common Name",
  "scientific_name": "If applicable",
  "confidence": 85,
  "toxicity_level": "TOXIC",
  "toxicity_color": "red",
  "symptoms": "...",
  "what_to_do": "...",
  "severity": "Seek vet attention within 2 hours",
  "extra_info": "..."
}
```

### Tested Accuracy (from our prototype)

- Monstera → correctly identified, correct toxicity (95% confidence)
- Snake Plant → correctly identified, correct toxicity (90% confidence)  
- Peace Lily → identified as Dieffenbachia (close relative, same toxin family — safety advice was still correct)
- Even when exact species ID is slightly off, toxicity category is usually correct because related plants share the same toxins

### Mobile Framework

- **React Native + Expo** OR **Flutter** — whichever you're faster with
- Camera: expo-camera or image_picker
- Subscriptions: RevenueCat SDK (handles both iOS and Android billing, free tier available)
- No backend server needed — API calls go directly from the app to Claude API

### What You Do NOT Need to Build

- No auth system (no login, no signup, no password reset)
- No user database (device-based, subscriptions handled by app stores)
- No backend server (direct API calls from app)
- No email verification
- No admin panel

---

---

## The Bigger Play (Next Vision Model)

This app is the first one. The architecture (onboarding → paywall → camera → LLM vision → result) is a **template** that can be reskinned for any identifier niche:

- Pet Safety Scanner (this app)
- Coin Identifier
- Rock Identifier  
- Bug Identifier
- Stamp Identifier
- Antique Identifier

Next Vision Limited runs 24 apps on this exact model and makes $3M/month combined. Each new app reuses 90% of the code — you just swap the system prompt, the onboarding copy, and the result screen design.

---

## Key Research Findings

### From Starter Story Analysis (34 founder interviews)

- The "copy and improve" method works: find something already making money, build it better
- Boring niches outperform sexy ones
- Speed matters: MVPs in days, not months
- Validation = people paying, not surveys

### From App Store Review Analysis (1,579 reviews across 30 identifier apps)

- 24/30 apps use predatory subscription tactics (auto-renew, hard to cancel)
- 28/30 apps have poor accuracy (15-40%)
- Safety info (is it venomous? is it edible?) is often paywalled — users hate this
- **Differentiation opportunity:** honest free trial, show confidence scores, never paywall safety-critical info

### Market Size

- Pet care app market: $2B in 2024, growing at 18% CAGR
- Pet tech market: $15.6B in 2025
- PictureThis (general plant ID) makes $5M/month in US alone
- CoinSnap (niche identifier) makes $1.4M/month
- Even small niches like stamps/antiques generate $10-30K/month per app

---

