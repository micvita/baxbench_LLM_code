library(dplyr)
library(stringr)
library(lubridate)
library(readr)


#' Bunch of routines to convert txt process files in CSVs
#' for trend analysis of VSZ and RSS.
#' TODO: POSIX Conversion can be done in the main trend file,
#' but having a column already converted in DATETIME
#' simplifies things at the moment.


get_cmd_from_env <- function(file_path) {

  filename <- tolower(basename(file_path))

  if (str_detect(filename, "fastapi|fast_api|aiohttp")) {
    return(c(
      "python(3)? .*app\\.py",
      "python(3)? app\\.py",
      "app\\.py",
      "code_prompt1.py",
      "COD_CREDIT_CARD1.py"
    ))
  }

  if (str_detect(filename, "open")) {
    return(c(
      "/\\.venv/bin/python3? (app\\.py|-c from multiprocessing\\.spawn import spawn_main; spawn_main\\(tracker_fd=\\d+, pipe_handle=\\d+\\) --multiprocessing-fork)$"
    ))
  }

  if (str_detect(filename, "flask")) {
    return(c(
      "code_prompt1.py",
      "COD_CREDIT_CARD1.py",
      "python(3)? app\\.py"
    ))
  }

  if (str_detect(filename, "django")) {
    return(c(
      "manage\\.py runserver 0\\.0\\.0\\.0:5000",
      "python(3)? .*manage\\.py runserver 0\\.0\\.0\\.0:5000",
      "\\.venv/bin/python3 .*manage\\.py runserver 0\\.0\\.0\\.0:5000"
    ))
  }

  if (str_detect(filename, "javascript|js|node")) {
    return(c(
      "^\\s*(/.*)?node\\b.*\\b(index|server)\\.js\\b"
    ))
  }

  if (str_detect(filename, "rust")) {
    return(c(
      "target/debug/[^ ]+"
    ))
  }

  warning(paste("Framework error!:", basename(file_path)))

  return(c(
    "python(3)? .*app\\.py",
    "python(3)? app\\.py",
    "app\\.py",
    "manage\\.py runserver 0\\.0\\.0\\.0:5000",
    "python(3)? .*manage\\.py runserver 0\\.0\\.0\\.0:5000",
    "\\.venv/bin/python3 .*manage\\.py runserver 0\\.0\\.0\\.0:5000",
    "^\\s*(/.*)?node\\b.*\\b(index|server)\\.js\\b",
    "target/debug/[^ ]+"
  ))
}

clean_single_txt_file <- function(
  file_path,
  output_dir = dirname(file_path),
  cmd_regex = NULL
) {

  if (is.null(cmd_regex)) {
    cmd_regex <- get_cmd_from_env(file_path)
  }

  lines <- readLines(file_path, warn = FALSE)

  lines <- gsub("\\s+", " ", lines)
  lines <- trimws(lines)
  lines <- lines[lines != ""]

  lines <- lines[!grepl(
    "\\bUSER\\b\\s+PID\\s+VSZ\\s+RSS\\s+%MEM\\s+COMMAND",
    lines
  )]

  parse_process_line <- function(line) {
    data <- unlist(strsplit(line, " "))

    if (length(data) < 7) {
      return(NULL)
    }

    data.frame(
      CURRENT_TIME = data[1],
      USER = data[2],
      PID = data[3],
      VSZ = data[4],
      RSS = data[5],
      MEM = data[6],
      COMMAND = paste(data[7:length(data)], collapse = " "),
      stringsAsFactors = FALSE
    )
  }

  parsed_lines <- lapply(lines, parse_process_line)

  # Remove NA or NULL elements
  parsed_lines <- parsed_lines[!sapply(parsed_lines, is.null)]

  if (length(parsed_lines) == 0) {
    warning(paste("No valid line found in:", file_path))
    return(NULL)
  }

  df <- do.call(rbind, parsed_lines)

  df$PID <- as.integer(df$PID)
  df$VSZ <- as.numeric(df$VSZ)
  df$RSS <- as.numeric(df$RSS)
  df$MEM <- as.numeric(df$MEM)

  combined_pattern <- paste(cmd_regex, collapse = "|")

  df_app <- df[grepl(
    combined_pattern,
    df$COMMAND,
    ignore.case = TRUE
  ), ]

  if (nrow(df_app) == 0) {
    warning(paste(
      "NO app found in:",
      basename(file_path),
      "with the following regex pattern:",
      combined_pattern
    ))
    return(NULL)
  }

  df_app$CURRENT_TIME_POSIX <- as.POSIXct(
    df_app$CURRENT_TIME,
    format = "%d/%m/%Y+%H:%M:%S"
  )

  df_app <- df_app[order(df_app$CURRENT_TIME_POSIX), ]


  file_name_no_ext <- tools::file_path_sans_ext(basename(file_path))
  output_path <- file.path(output_dir, paste0(file_name_no_ext, ".csv"))

  write.csv(df_app, file = output_path, row.names = FALSE, quote = TRUE)

  return(output_path)
}

clean_all_txt_files_routine <- function(
  basePath,
  output_dir
) {

  if (!dir.exists(output_dir)) {
    dir.create(output_dir, recursive = TRUE)
  }

  txt_files <- list.files(
    path = basePath,
    pattern = "\\.txt$",
    full.names = TRUE
  )

  if (length(txt_files) == 0) {
    warning(paste("No file .txt found in:", basePath))
    return(NULL)
  }

  cleaned_files <- lapply(txt_files, function(file_path) {

    cmd_regex <- get_cmd_from_env(file_path)

    clean_single_txt_file(
      file_path = file_path,
      output_dir = output_dir,
      cmd_regex = cmd_regex
    )
  })

  cleaned_files <- unlist(cleaned_files)

  return(cleaned_files)
}

# MAIN

basePath   <- file.path(getwd(), "Rscript/raw_txt_data/flask")
output_dir <- file.path(getwd(), "Rscript/raw_csv_data")

cleaned_app_files <- clean_all_txt_files_routine(
  basePath = basePath,
  output_dir = output_dir
)

cleaned_app_files