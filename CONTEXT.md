# Видео

A tablet program for watching videos from several video services in one place: finding them, following their channels, and keeping what one watched.

## Language

### Where videos come from

**Source**:
A video service the program can search and play from (YouTube, VK Видео). Each can be switched on or off.
_Avoid_: provider, service, backend

**Video**:
One playable item of a Source, with its title, Channel, duration and publication time.
_Avoid_: clip, item, media

**Short**:
A short vertical Video: YouTube's Shorts, VK's clips. Hidden unless the person turns them on.
_Avoid_: reel, clip (except VK's own name)

**Channel**:
Whoever publishes Videos on a Source: a YouTube channel, a VK community or person. The same publisher on two Sources is two Channels.
_Avoid_: author, owner, uploader, community

**Account**:
A person's own sign-in on a Source, which lets the Source pick for them and lists the Channels they follow there.
_Avoid_: login, profile, user

### What is the person's own

**Library**:
Everything that belongs to the person in the program, whichever Source it came from: the Followed channels, Bookmarks, History and settings.
_Avoid_: config, state, storage, profile

**Playlist**:
A named collection of Videos on a Source. Its Channel can differ from the Channels of the Videos it contains; a Bookmark opens its current contents.
_Avoid_: album, folder

**Bookmark** («Закладка»):
A saved Video, Playlist or Channel, independent of following. It can be local or linked to an Account's bookmarks; pinning and the order of pins belong to the program.
_Avoid_: favourite, subscription

**Followed channel**:
A Channel the person follows in this program. Following a Channel on an Account does not follow it here until the person brings the Account's Channels over.
_Avoid_: subscription (except in UI copy, «Подписки»), favourite

**History**:
The Videos the person played, most recent first, each once.
_Avoid_: recent, watched list

**Playback progress**:
The saved position of a Video, shared wherever it is opened. A Playlist separately remembers its last played Video and chosen order; opening it offers continuation without starting playback.
_Avoid_: history position, playlist page

**Scope**:
The Sources a search goes to: all switched-on Sources, or one of them.
_Avoid_: filter, search source

### What the person sees

**Feed** («Подписки»):
The newest Videos of all Followed channels together, newest first.
_Avoid_: subscriptions page, timeline

**Home** («Главная»):
The first page: what is new in the Feed, then what each Source finds popular or picks for the Account.
_Avoid_: start page, dashboard, trends
