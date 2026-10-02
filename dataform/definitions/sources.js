// Raw tables loaded by the Cloud Run functions. Declaring them lets other files use
// ${ref("raw", "<table>")}, so Dataform knows the dependencies and checks they exist.
const rawTables = [
  "diksha_content",
  "diksha_metrics_snapshot",
  "worldbank_indicators",
  "uis_indicators",
  "youtube_videos",
  "youtube_video_stats_snapshot",
  "youtube_channel_snapshot",
  "localization_plan_snapshot",
];

rawTables.forEach((name) => declare({ schema: "raw", name }));

declare({ schema: "ops", name: "ingestion_log" });
