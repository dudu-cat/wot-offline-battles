# World of Tanks Offline / LAN Battles 0.5.0

This release updates **both clients**:

- **0.8.2**
- **0.9.22.0.1 #1513 (Chinese HD)**

Download `WoT-Offline-Battles-Launcher-Windows.zip`, unpack it, and run
`WoT-Offline-Battles-Launcher.exe`. Select your game folder, select a mode
(single player / host / join), type a name, and click **Start game**. The
launcher installs the mod, writes the address, starts the server when one is
needed, and stops the server when the game closes. In the game, fit a tank and
click **Battle!**.

The previous releases were 0.4.0 (0.9.22) and 1.8.59 (0.8.2).

---

## Smoother battles

The sections below describe the 0.9.22 client. The 0.8.2 changes have their
own section.

- A fence, a shed or ground clutter no longer almost stops the tank, and the
  hull no longer heaves over the item it just flattened. A crushed item is
  gone, so you drive straight over it.
- Downhill braking works. Any slope you can hold now slows the tank when you
  release the throttle, exactly like flat ground.
- A destroyed tank leaves a wreck that blocks the road. You can no longer
  drive through it.
- Bots move smoothly instead of hopping from position to position. Distant
  bots move continuously too.
- A collision with a bot no longer makes both vehicles shake.
- A bot on a slope holds the correct hull pose. Its nose no longer digs into
  the ground, and point-blank shots hit it.
- Bots no longer reverse back and forth on the spot.
- Bot HE shells splash the vehicles beside the target. An SPG shell that
  grazes a track no longer does zero damage.
- Buildings and destructible items block sight lines, gun arcs and artillery
  trajectories. The battle destroys these items, and trees and poles fall.
- Several crashes are fixed: Alt+F4, killing an enemy, the end of the
  countdown, the assist accounting, and a failed damage-panel refresh.
- The memory a round uses is released at the end of the round. It no longer
  accumulates from round to round.

## The garage works (0.9.22)

- The garage is a real garage, and it saves. Your optional devices, crew
  skills, shells and consumables survive a restart.
- You own every vehicle in the client, and every module in that vehicle's own
  tech tree is unlocked. No experience is needed.
- Each vehicle already carries its top chassis, turret, gun, engine, radio and
  fuel tank, plus an automatic fire extinguisher, a large first aid kit and a
  large repair kit. Change any of them freely.
- Everything costs nothing.
- The parameters panel updates as soon as you fit something.
- The garage responds immediately. It no longer pauses after each click.
- Each crew member starts with three skill slots.
- The battle runs the vehicle the garage fitted, with the fitted modules,
  health, armour and gun.
- A vehicle change between rounds takes effect. The second round no longer
  runs the first round's tank.

## Your fittings take effect in battle (0.9.22)

- The numbers on the garage parameters panel are the numbers the battle uses.
  The battle could not read your fittings before.
- Crew skills and optional devices move view range, concealment, reload time,
  aim time, dispersion, turret and hull traverse, engine power, terrain
  resistance and repair speed.
- Binocular telescope and camouflage net need a few seconds without movement,
  as in the retail game.
- The binocular telescope and the coated optics do not stack. Only one
  applies.
- The concealment bonus of a camouflage is included.
- Crew injuries have a real effect. A dead gunner slows aiming and turret
  traverse, and a dead radio operator shortens the communication range.
- Intuition triggers. It loads the new shell at once and shows the stock
  message.
- Shell selection follows the retail rule. The first press queues the shell,
  and the second press switches at once and restarts the loading.
- Consumables are reusable, with a 90-second cooldown.
- Bots follow the same rules, with an untrained crew.

## What the battle shows (0.9.22)

- The enemy you aim at gets an outline. The outline was often missing before,
  even on a spotted target you were aiming at.
- The outline follows the vehicle the mouse really points at. It no longer
  marks the vehicle beside it, and it no longer stays on a vehicle forever.
- A burning vehicle shows fire.
- Your own tracks and road wheels turn, and the ground shows tracks.
- A destroyed vehicle leaves a wreck.
- An HE shell that lands on the ground shows an explosion, and the explosion
  is no longer stretched over hundreds of metres.
- A shot at an unspotted vehicle no longer shows armour-hit sparks. This
  matches the retail client.
- The marker of a destroyed vehicle changes to the destroyed state.
- The battle panel shows the icons of the devices you fitted, and the minimap
  circle is your real view range.
- The consumables panel shows ready, the remaining cooldown, or used up, and
  returns to ready by itself.
- The statistics are live. The damage log includes assists, and the spotting
  assist is a real number.
- The gun marker appears during the countdown. You still cannot fire until the
  battle starts.
- The SPG strategic view and trajectory view work. They no longer look up from
  under the ground.

## The room screen (0.9.22)

- The LAN room shows who is in the room, the map selection, start and leave.
- The room is drawn over the client's own queue screen. The cancel button of
  that screen leaves the room and returns you to the garage.
- The room no longer opens by itself. It appears only when you click
  **Battle!**.
- After a round, the first click on **Battle!** enters the room. Two clicks
  were needed before.
- An error during a round no longer disables the **Battle!** button
  permanently.
- The room panel, its buttons and the mouse pointer are drawn. This client
  never drew them.
- Every vehicle in the client is available in the room. The list stopped at
  the first 600 vehicles before, which cut the second half of the British,
  American and Soviet trees.

## The 0.8.2 client

- **A single-player battle also runs on the LAN server.** A single-player
  battle had no bots at all before, only a fake seven-second queue. The
  lineup, the bots and the pacing now come from the server, and the launcher
  starts a local server for you.
- **You select the map in the queue screen, and you can leave.** **START
  BATTLE** enters the map you selected, and **LEAVE** cancels the queue and
  returns you to the garage. The old behaviour dropped you into an unselected
  map a few seconds after **Battle!**.
- The client's own queue screen stays below the room instead of being
  replaced.
- **The client no longer rejects the foliage and navigation data.** It
  reported a checksum mismatch and then produced no bots.
- **A failed mod installation no longer blocks the game.** The installer
  removed the whole mod loader, so the game started unmodified and stopped at
  the login screen.
- The intro video is skipped. The game opens at the login screen.
- The LAN settings panel is removed from the garage. The launcher writes the
  address before the game starts.

## The launcher (both clients)

- One window: select the game folder, select a mode, type a name, start the
  game. The launcher installs the mod, writes the address, and starts and
  stops the server.
- The game folder list is remembered, and the launcher also searches the
  common installation paths.
- A **Test connection** button reports whether the host answers when you join,
  and whether another program holds TCP 28782 when you host.
- After you start hosting, the launcher prints this PC's LAN address for the
  other players.
- Other people's `.wotmod` files are left alone.
- Your own saved data is left alone: `offhangar_user` for 0.8.2, and the
  address, account state, garage and configuration for 0.9.22.
- The download is much smaller. Each client's files are one archive, and the
  installation unpacks only the archive you need.

## Known limits

- **Bot tracks and road wheels do not turn.** A broken enemy track has no
  visual feedback, only the damage-panel message. This needs the bots to
  become real vehicle entities in the client.
- **The client is a 32-bit program and can address about 2 GB.** This release
  closes several large memory leaks, but a very long session can still exhaust
  the address space. Restart the client between long sessions.
- **Only part of the crew-injury rules are implemented.**
- **One vehicle is unavailable**: the German VK 168.02 Mauerbrecher. It is the
  only one of the client's 680 vehicles that is missing its model files, and
  it crashes the client, so the garage and the bot lineup both skip it.

---

This project is released under the GNU GPL v3. It contains no World of Tanks
content and is not affiliated with Wargaming. Run the LAN server only on a
network you trust.
