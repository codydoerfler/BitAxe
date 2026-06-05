property recoverDir : "/Users/codydoerfler/BitAxe/recover"

on run
	set opts to {¬
		"1.  Flash a card or SSD   —   write Pi OS + config to a blank SD card or USB SSD", ¬
		"2.  Rebuild the stack   —   reinstall everything on the Pi + restore credentials", ¬
		"3.  Refresh credential backup   —   re-save tunnel / Tailscale / printer config", ¬
		"4.  Safe shutdown the Pi   —   graceful remote power-down (UPS-friendly)"}
	set pick to (choose from list opts ¬
		with title "BitAxe Pi Recovery" ¬
		with prompt "What do you want to do?" ¬
		OK button name "Run" cancel button name "Quit")
	if pick is false then return
	set pick to item 1 of pick

	if pick begins with "1" then
		set s to "flash-card.command"
	else if pick begins with "2" then
		set s to "rebuild-stack.command"
	else if pick begins with "3" then
		set s to "refresh-backup.command"
	else
		set s to "shutdown-pi.command"
	end if

	set scriptPath to recoverDir & "/" & s
	tell application "Terminal"
		activate
		do script "clear; bash " & quoted form of scriptPath
	end tell
end run
