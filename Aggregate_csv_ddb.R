#!/usr/bin/env Rscript

# Aggregazione out-of-core di CSV molto grandi con DuckDB.
# Il risultato contiene una riga per bucket temporale e mantiene il timestamp
# nella prima colonna in Unix millisecondi, come nel CSV client originale.
#
# Uso:
#   Rscript aggregate_large_csv_duckdb.R \
#     input.csv output_1min.csv [timestamp_col] [FUN] [period_minutes] \
#     [memory_limit] [temp_dir] [metric_columns]
#
# Esempio:
#   Rscript aggregate_large_csv_duckdb.R \
#     jmeter_50gb.csv jmeter_1min.csv timeStamp median 1 4GB ./duckdb_tmp AUTO
#
# metric_columns:
#   AUTO                   -> rileva automaticamente le colonne numeriche
#   "elapsed,Latency,..."  -> elenco esplicito separato da virgole

required_packages <- c("DBI", "duckdb")
missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, logical(1), quietly = TRUE)
]

if (length(missing_packages) > 0) {
  stop(
    "Pacchetti mancanti: ", paste(missing_packages, collapse = ", "),
    "\nInstallarli con: install.packages(c('DBI', 'duckdb'))"
  )
}

aggregate_large_csv <- function(input_csv,
                                output_csv,
                                timestamp_col = NULL,
                                FUN = "median",
                                period_minutes = 1L,
                                memory_limit = "4GB",
                                temp_dir = file.path(dirname(output_csv),
                                                     "duckdb_tmp"),
                                metric_columns = "AUTO",
                                delimiter = ",") {

  if (!file.exists(input_csv)) {
    stop("File di input non trovato: ", input_csv)
  }

  period_minutes <- as.integer(period_minutes)
  if (is.na(period_minutes) || period_minutes < 1L) {
    stop("period_minutes deve essere un intero >= 1")
  }

  FUN <- tolower(FUN)
  aggregate_sql_fun <- switch(
    FUN,
    median = "median",
    mean   = "avg",
    stop("FUN supportata: 'median' oppure 'mean'")
  )

  # Legge soltanto l'intestazione, non il contenuto del CSV.
  header <- names(utils::read.table(
    input_csv,
    header = TRUE,
    sep = delimiter,
    nrows = 0,
    check.names = FALSE,
    comment.char = "",
    quote = "\""
  ))

  if (length(header) < 2L) {
    stop("Il CSV deve contenere il timestamp e almeno una metrica")
  }

  if (is.null(timestamp_col) || !nzchar(timestamp_col)) {
    timestamp_col <- header[[1]]
  }

  if (!timestamp_col %in% header) {
    stop("Colonna timestamp non trovata: ", timestamp_col)
  }

  dir.create(dirname(output_csv), recursive = TRUE, showWarnings = FALSE)
  dir.create(temp_dir, recursive = TRUE, showWarnings = FALSE)

  con <- DBI::dbConnect(duckdb::duckdb(), dbdir = ":memory:")
  on.exit(DBI::dbDisconnect(con, shutdown = TRUE), add = TRUE)

  quote_id <- function(x) {
    as.character(DBI::dbQuoteIdentifier(con, x))
  }
  quote_str <- function(x) {
    as.character(DBI::dbQuoteString(con, x))
  }

  DBI::dbExecute(
    con,
    paste0("SET memory_limit = ", quote_str(memory_limit))
  )
  DBI::dbExecute(
    con,
    paste0("SET temp_directory = ", quote_str(normalizePath(
      temp_dir, winslash = "/", mustWork = TRUE
    )))
  )
  DBI::dbExecute(con, "SET preserve_insertion_order = false")

  input_sql  <- quote_str(normalizePath(input_csv, winslash = "/",
                                        mustWork = TRUE))
  output_abs <- normalizePath(output_csv, winslash = "/", mustWork = FALSE)
  output_sql <- quote_str(output_abs)
  delim_sql  <- quote_str(delimiter)

  # AUTO usa soltanto il CSV sniffer per individuare i tipi delle colonne.
  # La scansione completa sottostante usa all_varchar=true e TRY_CAST, così
  # valori numerici malformati diventano NULL in modo controllato.
  if (length(metric_columns) == 1L &&
      toupper(trimws(metric_columns)) == "AUTO") {

    schema_sql <- paste0(
      "DESCRIBE SELECT * FROM read_csv(", input_sql,
      ", header = true, delim = ", delim_sql,
      ", sample_size = 200000, ignore_errors = true)"
    )

    schema <- DBI::dbGetQuery(con, schema_sql)

    numeric_type_regex <- paste0(
      "^(UTINYINT|USMALLINT|UINTEGER|UBIGINT|TINYINT|SMALLINT|INTEGER|",
      "BIGINT|HUGEINT|FLOAT|REAL|DOUBLE|DECIMAL)"
    )

    metric_columns <- schema$column_name[
      grepl(numeric_type_regex, schema$column_type, ignore.case = TRUE)
    ]
    metric_columns <- setdiff(metric_columns, timestamp_col)

  } else {
    if (length(metric_columns) == 1L) {
      metric_columns <- trimws(strsplit(metric_columns, ",", fixed = TRUE)[[1]])
    }
    metric_columns <- metric_columns[nzchar(metric_columns)]
  }

  unknown_columns <- setdiff(metric_columns, header)
  if (length(unknown_columns) > 0L) {
    stop(
      "Colonne metriche non presenti nel CSV: ",
      paste(unknown_columns, collapse = ", ")
    )
  }

  metric_columns <- setdiff(unique(metric_columns), timestamp_col)
  if (length(metric_columns) == 0L) {
    stop(
      "Nessuna colonna numerica individuata. Specificare metric_columns ",
      "manualmente, ad esempio: 'elapsed,Latency,Connect'"
    )
  }

  q_timestamp <- quote_id(timestamp_col)
  q_metrics   <- vapply(metric_columns, quote_id, character(1))

  typed_metric_expr <- paste0(
    "TRY_CAST(", q_metrics, " AS DOUBLE) AS ", q_metrics
  )

  not_null_predicates <- c(
    "__timestamp_ms IS NOT NULL",
    paste0(q_metrics, " IS NOT NULL")
  )

  aggregate_expr <- paste0(
    aggregate_sql_fun, "(", q_metrics, ") AS ", q_metrics
  )

  interval_sql <- paste0(
    "INTERVAL '", period_minutes,
    if (period_minutes == 1L) " minute'" else " minutes'"
  )

  # Tutto avviene dentro DuckDB:
  # 1. lettura streaming del CSV;
  # 2. conversione dei tipi;
  # 3. eliminazione delle righe incomplete sulle metriche selezionate;
  # 4. aggregazione temporale;
  # 5. scrittura diretta del CSV ridotto.
  query <- paste0(
    "WITH typed AS (\n",
    "  SELECT\n",
    "    TRY_CAST(", q_timestamp, " AS BIGINT) AS __timestamp_ms,\n",
    "    ", paste(typed_metric_expr, collapse = ",\n    "), "\n",
    "  FROM read_csv(", input_sql, ",\n",
    "    header = true,\n",
    "    delim = ", delim_sql, ",\n",
    "    all_varchar = true,\n",
    "    ignore_errors = true\n",
    "  )\n",
    "), clean AS (\n",
    "  SELECT\n",
    "    make_timestamp_ms(__timestamp_ms) AS __timestamp,\n",
    "    ", paste(q_metrics, collapse = ", "), "\n",
    "  FROM typed\n",
    "  WHERE ", paste(not_null_predicates, collapse = " AND "), "\n",
    "), aggregated AS (\n",
    "  SELECT\n",
    "    time_bucket(", interval_sql, ", __timestamp) AS __bucket,\n",
    "    ", paste(aggregate_expr, collapse = ",\n    "), "\n",
    "  FROM clean\n",
    "  GROUP BY __bucket\n",
    ")\n",
    "SELECT\n",
    "  epoch_ms(__bucket) AS ", q_timestamp, ",\n",
    "  ", paste(q_metrics, collapse = ",\n  "), "\n",
    "FROM aggregated\n",
    "ORDER BY __bucket"
  )

  if (file.exists(output_abs)) {
    unlink(output_abs)
  }

  copy_sql <- paste0(
    "COPY (", query, ") TO ", output_sql,
    " (FORMAT CSV, HEADER TRUE)"
  )

  message("Input: ", normalizePath(input_csv, winslash = "/"))
  message("Metriche aggregate: ", paste(metric_columns, collapse = ", "))
  message("Funzione: ", FUN, " | bucket: ", period_minutes, " minuto/i")
  message("Elaborazione in corso tramite DuckDB...")

  DBI::dbExecute(con, copy_sql)

  message("CSV aggregato creato: ", output_abs)
  invisible(output_abs)
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)

  if (length(args) < 2L) {
    stop(
      paste(
        "Uso:",
        "Rscript Aggregate_duckdb.R",
        "<input.csv> <output.csv> [timestamp_col] [FUN] [period_minutes]",
        "[memory_limit] [temp_dir] [metric_columns]"
      )
    )
  }

  input_csv       <- args[[1]]
  output_csv      <- args[[2]]
  timestamp_col   <- if (length(args) >= 3L) args[[3]] else NULL
  FUN             <- if (length(args) >= 4L) args[[4]] else "median"
  period_minutes  <- if (length(args) >= 5L) args[[5]] else 1L
  memory_limit    <- if (length(args) >= 6L) args[[6]] else "4GB"
  temp_dir        <- if (length(args) >= 7L) args[[7]] else
    file.path(dirname(output_csv), "duckdb_tmp")
  metric_columns  <- if (length(args) >= 8L) args[[8]] else "AUTO"

  aggregate_large_csv(
    input_csv = input_csv,
    output_csv = output_csv,
    timestamp_col = timestamp_col,
    FUN = FUN,
    period_minutes = period_minutes,
    memory_limit = memory_limit,
    temp_dir = temp_dir,
    metric_columns = metric_columns
  )
}

main()