library(ggplot2)

#' Comparison plot function
#'
#' @param vut_1 Numeric vector. Values of the variable measured for the first
#'   application.
#'
#' @param vut_2 Numeric vector. Values of the variable measured for the second
#'   application.
#'
#' @param x_time_1 Numeric vector. Elapsed time from the first observation,
#'   expressed in minutes, for the first application.
#'
#' @param x_time_2 Numeric vector. Elapsed time from the first observation,
#'   expressed in minutes, for the second application.
#'
#' @param datain_1 Data frame containing the first dataset. It is used to
#'   retrieve the name of the variable represented in the plot.
#'
#' @param datain_2 Data frame containing the second dataset.
#'
#' @param col_1 Integer. Column index of the variable in the first dataset.
#'
#' @param col_2 Integer. Column index of the variable in the second dataset.
#'
#' @param label_1 Character. Label assigned to the first time series.
#'
#' @param label_2 Character. Label assigned to the second time series.
#'
#' @param output_dir Character. Directory where the SVG plot is saved.
#'
#' @param break_hours Numeric. Distance, in hours, between consecutive ticks
#'   on the x axis. Default is 2.
#'
#' @details
#' This function overlays two time series using line graphs. The elapsed time
#' vectors are converted from minutes to cumulative hours.
#'
#' The output filename is generated from the name of the variable contained
#' in the first dataset after sanitizing characters that may not be valid in
#' file paths.
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

  # Input validation
  if (length(vut_1) != length(x_time_1)) {
    stop("vut_1 and x_time_1 must have the same length.")
  }

  if (length(vut_2) != length(x_time_2)) {
    stop("vut_2 and x_time_2 must have the same length.")
  }

  if (!is.numeric(vut_1) || !is.numeric(vut_2)) {
    stop("vut_1 and vut_2 must be numeric vectors.")
  }

  if (!is.numeric(x_time_1) || !is.numeric(x_time_2)) {
    stop("x_time_1 and x_time_2 must be numeric vectors.")
  }

  if (!is.numeric(break_hours) ||
      length(break_hours) != 1 ||
      break_hours <= 0) {
    stop("break_hours must be a positive numeric value.")
  }

  variable_1 <- colnames(datain_1)[col_1]
  variable_2 <- colnames(datain_2)[col_2]

  if (is.na(variable_1) || is.na(variable_2)) {
    stop("Invalid column index.")
  }

  # Warning if the selected columns have different names
  if (!identical(variable_1, variable_2)) {
    warning(
      paste(
        "The selected variables have different names:",
        variable_1, "and", variable_2
      )
    )
  }

  # Data frames in long format
  data_1 <- data.frame(
    x = x_time_1,
    value = vut_1,
    application = label_1
  )

  data_2 <- data.frame(
    x = x_time_2,
    value = vut_2,
    application = label_2
  )

  data <- rbind(data_1, data_2)

  # Remove invalid observations
  data <- data[
    is.finite(data$x) & is.finite(data$value),
  ]

  if (nrow(data) == 0) {
    stop("No valid observations are available for plotting.")
  }

  # Order observations within each time series
  data <- data[
    order(data$application, data$x),
  ]

  # Convert elapsed time from minutes to hours
  data$hours <- data$x / 60

  # Sanitize variable name for the output filename
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

  if (upper_break == 0) {
    upper_break <- break_hours
  }

  # Overlaid line graph
  # Ensure a stable order of the two series
  data$application <- factor(
  data$application,
  levels = c(label_1, label_2)
)

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
  scale_color_manual(
    name = "",
    values = setNames(
      c("#050555", "red"),
      c(label_1, label_2)
    )
  ) +
  scale_linetype_manual(
    name = "",
    values = setNames(
      c("solid", "solid"),
      c(label_1, label_2)
    )
  ) +
  scale_x_continuous(
    breaks = seq(
      from = 0,
      to = upper_break,
      by = break_hours
    )
  ) +
  labs(
    title = paste(variable_1, "comparison"),
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

  svg(
    filename = file_path,
    width = 15,
    height = 5
  )

  print(ggp)

  dev.off()

  invisible(ggp)
}

open_path <- file.path(getwd(), "Rscript/raw_csv_data/processes_image-converter_open.csv")
ai_path <- file.path(getwd(), "Rscript/raw_csv_data/processes_image_converter_python_flask.csv")

open_server <- readr::read_csv(open_path, show_col_types = FALSE)
ai_server <- readr::read_csv(ai_path, show_col_types = FALSE)

time_format <- "%d/%m/%Y+%H:%M:%S"

open_date <- as.POSIXct(
  open_server[[1]],
  format = time_format,
  tz = "UTC"
)

ai_date <- as.POSIXct(
  ai_server[[1]],
  format = time_format,
  tz = "UTC"
)

open_x_time <- as.numeric(
  difftime(
    open_date,
    min(open_date, na.rm = TRUE),
    units = "mins"
  )
)

ai_x_time <- as.numeric(
  difftime(
    ai_date,
    min(ai_date, na.rm = TRUE),
    units = "mins"
  )
)

comparison_fun(
  vut_1 = open_server$VSZ,
  vut_2 = ai_server$VSZ,
  x_time_1 = open_x_time,
  x_time_2 = ai_x_time,
  datain_1 = open_server,
  datain_2 = ai_server,
  col_1 = which(
    colnames(open_server) == "VSZ"
  ),
  col_2 = which(
    colnames(ai_server) == "VSZ"
  ),
  label_1 = "ImageConverter FastAPI open-source",
  label_2 = "ImageConverter FastAPI LLM-generated",
  output_dir = "Rscript/comparison_plots",
  break_hours = 2,
  task = "image_converter_fast_api"
)