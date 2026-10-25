# Sisyphean

**Tagline:** A self-instrumenting agent runtime for ultra-long-horizon work in software that has no API.

## Inspiration
Most real software has no API. Every company's ERP is basically a modpack: custom fields, custom modules, nothing an MCP server covers. So today agents fall back to computer use, screenshots and clicks, which is slow, expensive, and falls apart on long tasks. Recent models can already make meaningful progress at building their own way into an app, and we believe that ability grows as models scale. We wanted to build the runtime that makes that the default.

## What it does
We built a runtime that enables the agent to bootstrap its own interface to any app. It starts with only pixels, mouse and keyboard, then writes code directly into the app. Our mutable tool server turns that code into live tools mid-run that can be iterated on, versioned and rolled back. Everything runs inside a guarded sandbox, and the interface keeps growing as tasks shift, so the agent gets faster and cheaper the longer it runs, while computer use gets slower and more expensive.

## How we built it
- **Sandbox:** the agent lives in a Docker container with one port out to the host. No LAN, and no host files beyond its workspace.
- **Mutable tool server (Python):** validates, loads, versions, health-checks and restores whatever the agent writes, and exposes it as new tools mid-run.
- **Runner:** wraps Claude and Codex for long runs, with context handoffs and checkpoints.
- **Graders:** read the real app state to score milestones and catch cheating when testing. These guardrails are simple to setup and allow for trustworthy long horizon runs
- **Live replay:** every event streams through SpacetimeDB into a React UI that replays or shows a live view of the run against the screen recording. It shows every tool the agent built with its code diffs, and charts tokens per milestone, tokens per action, actions per step, and how busy its hands are.

The core is fully general and there are no instructions or code specific to each application. Each app only needs an adapter to make sure edits stay contained and logs flow back to codex.

## Challenges we ran into
- Defining an "action" fairly. We settled on "would a human have had to input this", so every action must be a human action.
- Keeping the agent honest when the app itself would happily let it have privileged access..

## Accomplishments that we're proud of
Two games, two completely different extension systems (C# and Lua), the same runtime, and both goals hit overnight with zero intervention. And the goal change mid-run: the agent adapted its own tools instead of starting over.

## What we learned
Current generation models are very capable at a wide range of novel tasks, but they are constrained by the tools we give them. In recent weeks, models are finally capable of designing efficient ways of interaction solely through exploration of the environment.  Once the agent can build its own way in, the same model does far more per step, and it keeps getting cheaper instead of more expensive.

## What's next for Sisyphean
Test on a wider range of business software. We can use the same runtime, but the code it writes is an ERP plugin, deployed to a staging copy and reviewed like any other code.

## Built With
Python, TypeScript, React, Vite, SpacetimeDB, Docker, C#, Lua, Codex, Kerbal Space Program, Factorio, ffmpeg

**Repo:** https://github.com/JohnHeibel/self-instrumenting-harness
