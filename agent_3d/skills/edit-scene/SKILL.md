---
name: edit-scene
description: Propose bounded color or position changes to existing synthetic scene objects.
---

Read the current objects and revision before calling edit_objects.
Supply exact object ids, the expected revision and explicit final values.
Call edit_objects to produce native approval; asking for approval only in prose does not do this.
The native approval request is a proposal, not a completed edit.
After denial or revision conflict, do not retry a mutation without a new user request.
