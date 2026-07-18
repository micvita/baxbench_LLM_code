library(ggplot2)

#' Multi-series comparison plot function
#'
#' @param series A named list of series specifications. Each element must be
#'   a list with the following components:
#'   \itemize{
#'     \item \code{vut}: numeric vector of observed values.
#'     \item \code{x_time}: numeric vector of elapsed time (minutes), the
#'       same length as \code{vut}.
#'     \item \code{datain}: data frame used to retrieve the variable name.
#'     \item \code{col}: integer column index of the variable in
#'       \code{datain}.
#'     \item \code{label}: (optional) character label used in the legend.
#'       If omitted, the name of the list element is used instead.
#'   }
#'   At least two series are required. Example:
#'   \code{series = list(FastAPI = list(vut = ..., x_time = ..., datain =
#'   ..., col = ...), Django = list(...))}.
#'
#' @param output_dir Character. Directory where the SVG plot is saved.
#'
#' @param task Character. Identifier used in the output file name.
#'
#' @param break_hours Numeric. Distance, in hours, between consecutive ticks
#'   on the x axis. Default is 2.
#'
#' @param title Character. Optional custom plot title. If \code{NULL}
#'   (default), the title is built from the variable name of the first
#'   series.
#'
#' @param palette Character vector of colors, one per series, in the same
#'   order as \code{series}. If \code{NULL} (default), a qualitative,
#'   colorblind-conscious palette is generated automatically with
#'   \code{grDevices::hcl.colors()} (no extra package dependency).
#'
#' @param linetypes Character vector of line types, one per series. If
#'   \code{NULL} (default), all series are plotted with a solid line.
#'
#' @details
#' This function overlays an arbitrary number (two or more) of time series
#' on the same plot -- for example the same metric measured across every
#' framework/language considered in the study. It generalizes the original
#' two-series-only \code{comparison_fun()}, which is kept below as a thin
#' backward-compatible wrapper around this function.
#'
#' Elapsed time vectors are converted from minutes to cumulative hours.
#' Variable names are taken from the first series; a warning is raised if
#' any other series references a differently-named column (this can still
#' happen legitimately, e.g. comparing "VSZ" across frameworks that log it
#' under slightly different header names, but should usually be checked).
#'
#' @return Invisibly returns the ggplot object.
#'
comparison_fun_multi <- function(series,
                                  output_dir,
                                  task,
                                  break_hours = 2,
                                  title = NULL,
                                  palette = NULL,
                                  linetypes = NULL) {

  # --- Input validation ----------------------------------------------------
  if (!is.list(series) || length(series) < 2) {
    stop("`series` must be a list with at least two elements.")
  }

  required_fields <- c("vut", "x_time", "datain", "col")
  missing_fields <- lapply(
    series,
    function(s) setdiff(required_fields, names(s))
  )
  bad <- vapply(missing_fields, length, integer(1)) > 0
  if (any(bad)) {
    stop(
      "The following series (by position) are missing required fields (",
      paste(required_fields, collapse = ", "), "): ",
      paste(which(bad), collapse = ", ")
    )
  }

  if (!is.numeric(break_hours) ||
      length(break_hours) != 1 ||
      break_hours <= 0) {
    stop("break_hours must be a positive numeric value.")
  }

  # Labels: prefer explicit `label` field, fall back to list names
  series_names <- names(series)
  if (is.null(series_names)) series_names <- rep("", length(series))

  labels <- vapply(seq_along(series), function(i) {
    lab <- series[[i]]$label
    if (!is.null(lab) && nzchar(lab)) return(lab)
    if (nzchar(series_names[i])) return(series_names[i])
    paste("series", i)
  }, character(1))

  if (anyDuplicated(labels)) {
    stop("Series labels must be unique: ", paste(labels, collapse = ", "))
  }

  # --- Per-series validation + long-format assembly -------------------------
  variable_names <- character(length(series))
  data_list <- vector("list", length(series))

  for (i in seq_along(series)) {
    s <- series[[i]]

    if (!is.numeric(s$vut) || !is.numeric(s$x_time)) {
      stop(sprintf(
        "Series '%s': vut and x_time must be numeric vectors.", labels[i]
      ))
    }
    if (length(s$vut) != length(s$x_time)) {
      stop(sprintf(
        "Series '%s': vut and x_time must have the same length.", labels[i]
      ))
    }

    var_name <- colnames(s$datain)[s$col]
    if (is.na(var_name)) {
      stop(sprintf("Series '%s': invalid column index.", labels[i]))
    }
    variable_names[i] <- var_name

    data_list[[i]] <- data.frame(
      x = s$x_time,
      value = s$vut,
      application = labels[i]
    )
  }

  # Warn (do not fail) if series reference differently-named columns
  if (length(unique(variable_names)) > 1) {
    warning(
      "The selected series reference variables with different names: ",
      paste(unique(variable_names), collapse = ", ")
    )
  }
  variable_1 <- variable_names[1]

  data <- do.call(rbind, data_list)

  # Remove invalid observations
  data <- data[is.finite(data$x) & is.finite(data$value), ]

  if (nrow(data) == 0) {
    stop("No valid observations are available for plotting.")
  }

  # Stable factor order = order of `series` (not alphabetical)
  data$application <- factor(data$application, levels = labels)

  # Order observations within each series
  data <- data[order(data$application, data$x), ]

  # Convert elapsed time from minutes to hours
  data$hours <- data$x / 60

  # --- Output path -----------------------------------------------------------
  fname <- gsub("[^A-Za-z0-9_-]", "_", variable_1)

  comparison_dir <- file.path(output_dir, "comparison")

  dir.create(
    comparison_dir,
    recursive = TRUE,
    showWarnings = FALSE
  )

  file_name <- paste0("comparison_", fname, "_", task, ".svg")
  file_path <- file.path(comparison_dir, file_name)

  max_hour <- max(data$hours, na.rm = TRUE)
  upper_break <- ceiling(max_hour / break_hours) * break_hours
  if (upper_break == 0) upper_break <- break_hours

  # --- Palette and linetypes --------------------------------------------------
  n <- length(labels)

  if (is.null(palette)) {
    # Base-R-only qualitative palette (no extra dependency). "Dark 3" gives
    # good separation for a handful of series and degrades gracefully as n
    # grows (e.g. for the 6 frameworks, or up to ~21 configurations).
    palette <- grDevices::hcl.colors(n, palette = "Dark 3")
  } else if (length(palette) != n) {
    stop("`palette` must have the same length as `series` (", n, ").")
  }
  names(palette) <- labels

  if (is.null(linetypes)) {
    linetypes <- rep("solid", n)
  } else if (length(linetypes) != n) {
    stop("`linetypes` must have the same length as `series` (", n, ").")
  }
  names(linetypes) <- labels

  # --- Plot --------------------------------------------------------------------
  plot_title <- if (is.null(title)) paste(variable_1, "comparison") else title

  ggp <- ggplot(
    data,
    aes(
      x = hours,
      y = value,
      color = application,
      linetype = application,
      group = application
    )
  ) +
    geom_line(
      linewidth = 0.7,
      alpha = 0.85,
      na.rm = TRUE
    ) +
    scale_color_manual(name = "", values = palette) +
    scale_linetype_manual(name = "", values = linetypes) +
    scale_x_continuous(
      breaks = seq(
        from = 0,
        to = upper_break,
        by = break_hours
      )
    ) +
    labs(
      title = plot_title,
      x = "Time (hours)",
      y = variable_1
    ) +
    theme_linedraw() +
    theme(
      axis.text.x = element_text(
        angle = 45,
        hjust = 1
      ),
      legend.position = "bottom"
    )

  # Widen the canvas a bit as the number of series (and legend entries)
  # grows, so the bottom legend does not get too cramped.
  plot_width <- max(15, 15 + 0.4 * max(0, n - 6))

  svg(
    filename = file_path,
    width = plot_width,
    height = 5
  )

  print(ggp)

  dev.off()

  invisible(ggp)
}


#' Comparison plot function (two series) -- backward-compatible wrapper
#'
#' Kept for compatibility with existing call sites. Internally delegates to
#' \code{comparison_fun_multi()}. See that function for the generalized
#' N-series version (e.g. all frameworks/languages at once).
#'
#' @inheritParams comparison_fun_multi
#' @param vut_1,vut_2 Numeric vectors. Values of the variable measured for
#'   the first and second application.
#' @param x_time_1,x_time_2 Numeric vectors. Elapsed time from the first
#'   observation, in minutes, for the first and second application.
#' @param datain_1,datain_2 Data frames used to retrieve the variable name.
#' @param col_1,col_2 Integer. Column index of the variable in each dataset.
#' @param label_1,label_2 Character. Labels for the two series.
#'
#' @return Invisibly returns the ggplot object.
#'
comparison_fun <- function(vut_1, vut_2,
                            x_time_1, x_time_2,
                            datain_1, datain_2,
                            col_1, col_2,
                            label_1 = "Manual application",
                            label_2 = "LLM-generated application",
                            output_dir,
                            break_hours = 2,
                            task) {

  comparison_fun_multi(
    series = list(
      list(vut = vut_1, x_time = x_time_1, datain = datain_1, col = col_1,
           label = label_1),
      list(vut = vut_2, x_time = x_time_2, datain = datain_2, col = col_2,
           label = label_2)
    ),
    output_dir = output_dir,
    task = task,
    break_hours = break_hours,
    # Preserves the original navy/red look of the two-series plots.
    palette = c("#050555", "red")
  )
}


#' Compare a metric across all frameworks/languages
#'
#' Convenience wrapper around \code{comparison_fun_multi()} for the common
#' case of overlaying the same metric across every framework/language
#' considered in the study, each stored in its own CSV file with a
#' timestamp as the first column.
#'
#' @param file_paths Named character vector or list. Names are used as
#'   series labels (e.g. framework/language names, such as
#'   \code{"FastAPI"}, \code{"Django"}, \code{"Express"}, ...); values are
#'   the paths to the corresponding CSV files.
#' @param metric Character. Name of the column to plot. Must exist, with
#'   the same name, in every CSV file.
#' @param output_dir,task,break_hours,title,palette,linetypes See
#'   \code{comparison_fun_multi()}.
#' @param time_format Character. \code{POSIXct} format string used to parse
#'   the timestamp column (assumed to be the first column of each CSV).
#'   Defaults to the format used elsewhere in this project
#'   (\code{"%d/%m/%Y+%H:%M:%S"}).
#'
#' @details
#' Each CSV is read with \code{readr::read_csv()}, its first column is
#' parsed as a timestamp and converted to elapsed minutes from the first
#' observation, and the requested \code{metric} column is extracted. The
#' resulting series are then passed to \code{comparison_fun_multi()}.
#'
#' @return Invisibly returns the ggplot object.
#'
comparison_fun_all_frameworks <- function(file_paths,
                                           metric,
                                           output_dir,
                                           task,
                                           break_hours = 2,
                                           title = NULL,
                                           palette = NULL,
                                           linetypes = NULL,
                                           time_format = "%d/%m/%Y+%H:%M:%S") {

  if (is.null(names(file_paths)) || any(!nzchar(names(file_paths)))) {
    stop(
      "`file_paths` must be a named vector/list; names are used as series ",
      "labels (e.g. framework/language names)."
    )
  }

  series <- lapply(names(file_paths), function(lab) {
    d <- readr::read_csv(file_paths[[lab]], show_col_types = FALSE)

    if (!metric %in% colnames(d)) {
      stop(sprintf(
        "Column '%s' not found in the file provided for '%s'.", metric, lab
      ))
    }

    ts <- as.POSIXct(d[[1]], format = time_format, tz = "UTC")

    x_time <- as.numeric(
      difftime(ts, min(ts, na.rm = TRUE), units = "mins")
    )

    list(
      vut = d[[metric]],
      x_time = x_time,
      datain = d,
      col = which(colnames(d) == metric),
      label = lab
    )
  })
  names(series) <- names(file_paths)

  comparison_fun_multi(
    series = series,
    output_dir = output_dir,
    task = task,
    break_hours = break_hours,
    title = title,
    palette = palette,
    linetypes = linetypes
  )
}

# --- New: same metric across every framework/language ----------------------
# One entry per framework -- adjust names/paths to match your actual
# raw_csv_data files. The names become the legend labels.
comparison_fun_all_frameworks(
  file_paths = c(
    "FastAPI" = "Rscript/raw_csv_data/processes_uptime_python_fast_api.csv",
    "Django"  = "Rscript/raw_csv_data/processes_uptime_python_django.csv",
    "Flask"   = "Rscript/raw_csv_data/processes_uptime_python_flask.csv",
    #"aiohttp" = "Rscript/raw_csv_data/python_resource_uptime_python_aiohttp.csv",
    "JS_Express" = "Rscript/raw_csv_data/processes_uptime_js.csv",
    "Rust_Actix"   = "Rscript/raw_csv_data/processes_uptime_rust.csv"
  ),
  metric = "RSS",
  output_dir = "Rscript/comparison_plots",
  task = "uptime_all_frameworks"
)
