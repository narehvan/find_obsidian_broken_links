# find_obsidian_broken_link.py
This script is specifically for Obsidian note to help search for files that meet the two conditions below. 

This script does two actions:
- Searches for Obsidian note files in the vault that contain a wikilink type link that points to a non-existing file
- Searches for other files than Obsidian note files (e.g., `.jpg`, `.png`, `.pdf`) that are orphan (they are not mentioned/linked in any Obsidian note)

# Why this script?
If Obsidian is running in "Restricted Mode" and/or one does not wish to install a Community Plugin to serve the same purpose.

# Requirements
Python 3.7+

# Usage
```
python3 find_obsidian_broken_link.py <absolute path to the Obsidian vault>
```
