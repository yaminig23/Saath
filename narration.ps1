# Local narration bridge (Windows Speech -> WAV). No network, no cost.
param([string]$TextFile = "", [string]$OutFile = "", [string]$Voice = "", [int]$Rate = -1, [switch]$ListVoices)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($ListVoices) {
  $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name + "|" + $_.VoiceInfo.Culture.Name }
  exit 0
}
if (-not $TextFile -or -not $OutFile) { throw "TextFile and OutFile are required" }
if ($Voice) { $synth.SelectVoice($Voice) }
$synth.Rate = $Rate
$text = [IO.File]::ReadAllText($TextFile)
if (-not $text.Trim()) { throw "Nothing to narrate" }
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($text)
$synth.SetOutputToNull()
$synth.Dispose()
