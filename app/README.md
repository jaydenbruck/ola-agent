# Ola for iPhone

Fresh SwiftUI app for the N-0 API, iOS 17 or later. One application target, `Ola`.

Open `Ola.xcodeproj`, select the Ola scheme and an iPhone simulator, then Run.
For a phone, set your development team and signing profile. No credentials are in the project.
In Settings, enter the server URL and bearer token. Both are stored in the device Keychain.
Use an address reachable from the phone, not the computer's loopback address.
Local HTTP servers are supported. Use HTTPS for a remote server.

Run the parser, event reducer, persistence, and frame coordinate tests on a Swift 5.9+ host:

```sh
cd app
swift test
xcodebuild -project Ola.xcodeproj -scheme Ola -sdk iphonesimulator -configuration Debug CODE_SIGNING_ALLOWED=NO build
```

The root `codemagic.yaml` runs both commands on a Mac and retains the unsigned simulator app.
The app is compiled separately from the portable test package so the Xcode project has one target.

## Try each path

1. Configure the server. Send a German request with bold/list output. Select part of the growing reply. Repeat in English.
2. Attach a photo with plus, send it, and ask what it shows.
3. Ask for several jobs. Watch one card per job, state, step, and screenshot. Stop one job.
4. When a card waits for you, tap Take over. Tap the remote page, enter text, scroll, and press Done, carry on. Frames poll at two requests per second when the network keeps up. Controls stay outside the image; letterbox taps are ignored.
5. Tap the mic. The waveform reflects captured samples. X discards, square puts text in the composer, arrow sends. Speech recognition requires an on-device language model and microphone/speech permission.
6. Turn on the speaker. Completed replies use the detected reply language. Dictating stops playback.
7. Background and reopen the app. The locally saved thread reappears; the SSE cursor requests replay and job history restores the cards.

The backend retains 500 replay events in memory. A server restart or a longer gap can lose unfinished streaming text. A completion event's full text repairs its reply. Chat history is local to this device and connection. The screenshot does not expose the page's accessibility tree; VoiceOver has labeled controls and scroll actions, but arbitrary remote page targets still require sight.

Build success is separate from device acceptance. Microphone, spoken language, selection, keyboard layout, and the real authenticated takeover must be checked on a phone before reporting them as witnessed.
