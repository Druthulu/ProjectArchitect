// 3.30.2: the release this mod's code is. The root install (pa/install/root.py) stamps this file in the installed mod
// with the package's version and commit, so a running process knows the code it loaded, whatever the files on disk say
// since: the session-start hook's background install can rewrite VERSION and MANIFEST.json while a session starts. In a
// checkout (tests, a dev run) it stays empty, and the installed VERSION file stands in.
export const BUILD: { version: string; git: string } = { version: '', git: '' }
