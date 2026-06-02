library(readr)

runs_path <- file.path(getwd(), "Rscript", "runs.txt")

runs <- read_csv(runs_path, show_col_types = FALSE)

script_path <- file.path(getwd(), "Rscript", "SW_Aging_Trend_Analysis_script.R")

for (i in seq_len(nrow(runs))) {
  run_name <- paste(runs$task[i], runs$env[i], sep = "_")

  cat("\nRunning:", run_name, "\n")

  status <- system2(
    command = "Rscript",
    args = c(
      script_path,
      runs$server_csv[i],
      runs$client_csv[i],
      runs$task[i],
      runs$env[i]
    )
  )

  if (status != 0) {
    warning("Failed: ", run_name, call. = FALSE)
  }
}