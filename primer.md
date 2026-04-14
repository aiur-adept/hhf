# Hatched Horses Fighting

([link to simulation code repository](https://github.com/aiur-adept/hhf))

![hay bale food token](https://i.imgur.com/t7Mv6AT.png)

It's hatched horses fighting, need I say more?

## The Core Loop

This deck "works" by having horses that fight come out of eggs.

### Engine 1 - Crested Sunmare Token Factory
> *"If you gained life this turn, create a 5/5 white Horse token at end step."*

**Crested Sunmare** is the deck's most powerful card. Once it's on the battlefield with any lifelink source — **Basilisk Collar**, **Shadowspear**, **Loxodon Warhammer**, **Resurrection Orb**, or **Shield Mare**'s triggered ability — you can generally generate a 5/5 Horse every turn. Every Horse also becomes indestructible while Sunmare is alive, so ideally you can swiftfoot and/or resurrection orb the crested sunmare. 

Is this engine a game-winner? Not really, maybe in a 1v1 with a deck that can't keep pace, but it is nonetheless one of the more powerful outcomes and worth mentioning. This is what you'll tutor for with **Eladamri's Call** / the other creature tutors virtually every time.

### Engine 2 — Fight Spells as Removal
The deck runs > 10 fight/bite spells. These do double duty: they remove blockers and threats while also being totally fucking rad. It's horses fighting! Give that horse a sword, or a basilisk collar, and we're talking.

---

## Game Plan by Phase

### Early Game (Turns 1–3): Establish Mana and a Horse
- Play ramp.
- Get a cheap horse onto the board
- Use fight spells early on if somebody drops a creature that is part of a potential value engine, or just because it's fun as hell to see our horses fight.

**IMPORTANT NOTE**: You should never, ever send a horse into a fight you know it won't win. That is wrong.

### Mid Game (Turns 4–7): Build the Herd
This is where the deck opens up. You want to assemble two or three of the following:

**Atla with eggs on board**

**The life-gain package** (to enable Crested Sunmare)

**Generic tribal / buffs**

**The card draw pieces**

**Equipped Horses**

Having that assembled, you will want to be using your fight spells sparingly or at least politically. There's an old adage about how single target removal is bad in commander due to the math of opponents. This might be true, but it's also possible - putting aside the flavour and the fun of horses fighting - that you could trade the services of your horses for immunity or to generally make allies. This protects you despite your jankiness until you can hope to assemble a more impressive board to genuinely compete for top spot. If you get a large enough herd, you can be threatening enough to swing against, especially if you cultivate a reckless attitude, declaring you will swing back full force, leaving yourself open, especially if you sense the other players might let you do this.

I feel sick with myself for analyzing winning; we're losing focus on what truly matters. It's about hatched horses fighting. Okay, continuing on...

### Late Game (Turns 8+): Close It Out
At this point you should have 6–10 creatures if you FOR SOME REASON have been allowed to exist as an obvious horse-threat. Your finishers:

- **Craterhoof Behemoth**
- **Colossification** and **Fireshrieker** on a trample horse.
- **Collective Blessing** making every single horse at least a 4/4 (often a fair bit larger)

Your most realistic chance of winning is if you are presenting several of these.

--- 

## What to Do Against Wraths

If you sense that a boardwipe is coming, or just want insurance, try to invest more in eggs than anything else. The eggs will pop after the boardwipe resolves, and atla's ability will trigger for each dying egg. 

## Skullclamp and Calamity, Galloping Inferno

Both these cards offer you alternative ways to pop the eggs.

You will want to skullclamp the eggs, but it is very important not to skullclamp the horses. Again, that's not right.

As for Calamity, you can saddle with creatures of zero power! So saddle with the eggs, copy the eggs, then you sac the egg copies at the end step.

## Calamity & Crested Sunmare

If you saddle with the Sunmare and copy it twice (it's not legendary), you decide the order of triggers in the end step, so you can get 3 horses if you gained life (6 if roaming throne naming Horse is out).

## Roaming Throne

Choose Shaman if you want double egg pops, choose Horse if you have a reason to.

## Delney's most overpowered contribution

Bill the Pony will enter with 4 foods thanks to Delney's ability. With parallel lives that's 8!

## Seedborn muse

Use her with Atla to make 4 eggs each turn cycle.

## Crested Sunmare + Lifelink/Lifegain engine analysis

I vibe coded a python program to run 100,000 games, simulating board state and card draw mechanics. This gave me the following data:

![card draw analysis - we draw 24 cards by turn 10 on average, with standard deviation 7.5 cards](https://i.imgur.com/e7Jz1V3.png)

Then I vibe coded a python program to run 100,000 games statistically simulating land drops, ramp, and casting, to see by what turn we can expect to get the deck's best engine online, given the card draw statistics above. The results are modest and interesting, showing the deck is tuned to definitely-playable consistency:

![crested sunmare engine analysis - 24% on turn 5, up to 74% on turn 10](https://i.imgur.com/DM4DPyB.png)

Finally we have a gameplan analysis that simulates gameplay on my turns and sees - given the cards i drew - could i have gotten each of the deck's gameplans online by that turn. 2.8% of the time, we don't get a gameplan online, and the best signal for a bad hand is having only non-creature spells.

![gameplan analysis table](https://i.imgur.com/eFoSKDp.png)

Finally, we have a simulation that analyses median power and toughness up to T10. We see 4/4 on T4 scaling linearly more or less up to 26/27 on T10. IQR goes from 3 on T4 to 21 on T10 (that is, some games do substantially better; in fact 23.4% of games reached >= 40 power by T10).

![board power & toughness statistics table](https://i.imgur.com/FcTWkuP.png)

## The Most Important Question

Does Atla Palani herself lay the eggs? She taps, an egg appears, I'm just saying. I can have an empty battlefield except for Atla, and I can still get eggs. No horses are around to lay them. The only animal capable (somehow) of laying the eggs, is Atla. It seems logically that she makes the eggs. This explains why Selvala, Explorer Returned has a chance of popping out of one, since she's also a humanoid. It doesn't explain the horses.