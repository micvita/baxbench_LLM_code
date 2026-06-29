library(readr)

#' Script to run multiple Trend Analysis based on the runs.txt file.

runs_path <- file.path(getwd(), "Rscript", "runs_open.txt")

runs <- read_csv(
  runs_path,
  show_col_types = FALSE,
  na = character()
)

script_path <- file.path(getwd(), "Rscript", "SW_Aging_Trend_Analysis_script.R")

clean_path <- function(x) {
  if (length(x) == 0 || is.na(x) || trimws(x) %in% c("", "NA")) {
    return("")
  }
  trimws(x)
}

for (i in seq_len(nrow(runs))) {
  run_name <- paste(runs$task[i], runs$env[i], sep = "_")

  cat("\nRunning:", run_name, "\n")

  server_csv  <- clean_path(runs$server_csv[i])
  client_csv  <- clean_path(runs$client_csv[i])
  process_csv <- clean_path(runs$process_csv[i])

  status <- system2(
    command = "Rscript",
    args = c(
      script_path,
      server_csv,
      client_csv,
      process_csv,
      runs$task[i],
      runs$env[i]
    )
  )

  if (status != 0) {
    warning("Failed: ", run_name, call. = FALSE)
  }
}