# The AegisFlow demonstration

The seven steps from the specification (§37), done in the dashboard. Every observation below was recorded from a real run
of the Docker stack; nothing here is staged. For the same flow without clicking, run `python scripts/demo_flow.py`.

**Before you start:** `docker compose up -d --build`, open http://localhost:8080, sign in with the **admin** token.

## Step 1: start the system

In the **Demo** panel choose **10 devices** and a speed (20× is comfortable), then **Start fleet**.

*What you see:* a progress bar, "Building each device's baseline". The system is fast-forwarding **34 simulated hours**
through the real pipeline so every device has learned its own normal behaviour; without that it would refuse to raise
alerts, deliberately. It takes about **a minute**. Then the map fills with ten devices, the status pill says **Live**, and
the header shows the site clock (it starts on a Tuesday at 10:00, working hours).

## Step 2: show baseline activity

Watch the map for a minute. Devices drift between the Office, Meeting Room and Lab and sit quietly; all show **Normal**.
Click a device in the list: its score is low (about 0.1) and its four signal bars are near zero.

*Honest note:* occasionally a device you did not touch is flagged (for example "connection count is 3.2 standard deviations
above normal"). That is the system's measured false-alarm rate at work, about five alert onsets per device per day for the
learned fusion (manual §5.7). It is a cost of the method, not a fault in the demo.

## Step 3: trigger a spatial event

Select a device (say **D-004**), then **Enter restricted zone** (strength 0.80 or higher).

*What you see:* the device walks across the site to the hatched **ServerRoom**. Within seconds its marker turns red and
pulses, the score jumps past the alert line on its chart, and an alert appears at the top of the feed: **High: entered a
restricted zone**. The map draws the path it took.

## Step 4: add other deviations

On the same device click, a couple of seconds apart: **Unknown connections**, **Battery drain / CPU**, **Unknown device
nearby**.

## Step 5: show correlation

Watch the device panel. The four signal bars (Location, Network, Device health, Proximity) light up one after another, in
their own colours. The feed reports **"Another signal joined"** as each is added, and the alert's count grows to
**4 signals**. This is the point of the system: no single event proves anything; several changing together do.

## Step 6: explain the alert

Read **Why it was flagged**. In a real run it said:

1. **Location** (contributes 0.38): *entered a restricted zone*
2. **Proximity** (0.31): *unknown (unenrolled) devices are nearby, 6.0 standard deviations above normal*
3. **Network** (0.30): *contacting endpoints it does not normally use (29 standard deviations above normal); connection
   count is 8.4 above normal; upload volume is 7.4 above normal; download volume is 5.0 above normal*
4. **Device health**: *temperature 4.1, CPU load 3.9, battery draining 3.0 standard deviations above normal*

and, always, *"the score ranks how unusual this is; it is not a probability that an attack occurred."*

## Step 7: restore normal behaviour

Click **Restore normal**.

*What you see:* the device walks back to the Office, its signals fall away, the score decays, the marker returns to grey
and the alert closes. The slowest signal is battery drain, which is judged over a 5-minute window, so full recovery takes about
**16 seconds** at 20× speed (about five simulated minutes).

## Other things worth showing

- **Analytics:** risk by device, alerts by severity and by leading signal, processing latency (typically a few milliseconds).
  **Show data as tables** switches every chart to a table.
- **Evidence:** the final test's pre-registered comparisons with their intervals, including the ones that did *not* come out
  in the project's favour.
- **Viewer role:** sign out and in with the viewer token. Everything is visible; every control is gone, and the server would
  refuse the request anyway.
- **Light theme:** the **Theme** button.

## If something looks wrong

| Symptom | Likely cause |
|---|---|
| "Start fleet" is missing | A demo is already running. Press **Stop**. |
| Nothing alerts | The baselines are not ready (still warming), or the device was not selected. |
| Sign-in fails | Wrong token, or too many failed attempts in a short time (wait a minute). |
| Compose will not start | `.env` is missing or its tokens are empty, shorter than 16 characters, or identical. |
| Port 8080 is taken | Set `AEGIS_PORT` in `.env`. |
