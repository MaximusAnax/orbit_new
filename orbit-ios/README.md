# Orbit iOS

SwiftUI client for Orbit.

## Setup

1. Open `Orbit.xcodeproj` in Xcode 15+
2. Set scheme environment variable `API_BASE_URL` to your backend (default `http://127.0.0.1:8000`)
3. Configure Supabase Auth SDK when enabling production auth

## Structure

```
Orbit/
  App/           OrbitApp entry
  Features/      Capture, Review, Recall, Discover
  Networking/    APIClient, UploadQueue
  Models/        API types
  DesignSystem/  Theme
```

## Features

- **Capture:** Voice record (offline queue) or text submit → 202 Accepted
- **Review:** Proposal accept/reject/defer
- **Recall:** Profile + Catch me up
- **Discover:** Network search and semantic search

## Tests

```bash
xcodebuild test -scheme Orbit -destination 'platform=iOS Simulator,name=iPhone 16'
```
