# Reminder Templates (Canonical)

## 13:00 (Berlin time)

Hey 👋 wie es aussieht fehlt heute noch dein Essen-Tracking.
Aktuell fehlen z. B.: {missingMealsText}.
Bis 13 Uhr wären ca. {proteinMinimumBy13} g Protein sinnvoll (3/8 Ziel), aktuell: {proteinConsumedGrams} g.
Kalorien bisher: {caloriesConsumed}/{caloriesTarget} kcal.

## 19:00 (Berlin time)

Kurzer Check-in ✅
Heute getrackt: {mealsLoggedText}. Passt das so?
Du bist noch {underOrOver} deinem Proteinziel ({proteinConsumedGrams}/{proteinGoalGrams} g).
Heute: {caloriesConsumed}/{caloriesTarget} kcal (offen: {remainingCalories} kcal).
Proteine sind wichtig in der Diät 💪 Vorschlag: {idea1} oder {idea2}.

Notes:
- 13:00 reminder trigger is still conditional (only if no food tracked in the window), but now includes the 3/8 protein checkpoint.
- 19:00 reminder trigger: only when proteinDeficitGrams > 0.
