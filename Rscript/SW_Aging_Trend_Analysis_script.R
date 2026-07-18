library(readr)
library(trend)
library(zyp)
library(randtests)
library(ggplot2)
library(xts)
library(zoo)
library(highfrequency)
library(TSstudio)
library(modifiedmk)
library(sjmisc)
library(forecast)


#' Trend analysis function
#'
#' @param exclude_list Integer vector that contains the column indices to
#'   exclude from the analysis, usually timestamp columns or variables
#'   that should not be tested.
#'
#' @param csv_path Character parameter that contains the path to the CSV file to
#'   analyze.
#'
#' @param task_name Character parameter that contains the name of the task
#'   (e.g. monitor, UpTimeService, ...).
#'
#' @param env_name Character parameter that contains the name of the environment
#'   (e.g. python_django, python_fastapi, ...).
#'
#' @param row_cutoff Integer. Last row to keep from the input CSV, used to align
#'   client and server datasets to the same time window (51 hour experiment in
#'   my case).
#'
#' @param aggregate Logical value. TRUE means that all the time series in the
#'   CSV file need to be aggregated (used for client CSVs).
#'
#' @param dt_position Integer. Index of the column containing the timestamps/
#'   datetimes of the parameters inside the CSVs.
#'
#' @param aggr_FUN Read highfrequency::aggregateTS() "FUN" parameter
#'   documentation.
#'
#' @param aggr_align Read highfrequency::aggregateTS() "alignBy" parameter
#'   documentation. Default value is "minutes".
#'
#' @param aggr_period Specifies the size of the "aggregation bucket". Default is
#'   1, so leaving all default values for both "aggr_align" and "aggr_period"
#'   aggregates data in 1 minute intervals. Read highfrequency::aggregateTS()
#'   "alignPeriod" parameter documentation.
#'
#' @param plot_dir Character. Directory where trend, ACF, and PACF plots are
#'   saved.
#'
#' @param tz Character. Time zone used for timestamp conversion.
#'
#' @return A dataframe containing the statistical results for each analyzed
#'   variable, including p-values, trend flags, Sen slopes, confidence intervals
#'   for Sen, autocorrelation diagnostics, and modified Mann-Kendall p-values.
#'
#' @details
#' This function performs a multi-step trend analysis on the time series
#' contained in the input CSV file. The goal is to identify statistically
#' relevant trends that may indicate software aging phenomena.
#'
#' The analysis proceeds as follows:
#'
#' 1. The input dataset is read with readr::read_csv(). Parsing issues are
#'    logged using readr::problems(). Malformed rows are handled as follows:
#'    client-side CSV files are usually larger and may contain malformed rows
#'    due to high-frequency sampling of response times. In such cases the
#'    function simply remove the malformed rows.
#'    Server-side CSV files are more tricky because they are smaller.
#'    For this reason server-side parsing errors are only logged at the moment.
#'
#' 2. Each non-excluded variable/CSV column is analyzed separately. Client-side
#'    time series are aggregated into one-minute intervals because server CSV
#'    files are already sampled approximately once per minute. This ensures
#'    coherent results for both server and client parameters and is also less
#'    computationally demanding for statistical tests.
#'
#' 3. Initial trend detection is based on a 2 out of 3 heuristic using three
#'    non-parametric tests: Cox-Stuart, Spearman rho, and Mann-Kendall. If at
#'    least two of these tests have a p-value below 0.05, the time series is
#'    classified as having a statistically significant monotonic trend. All
#'    tests are "two-tailed", so at this step the function does not
#'    specify the direction of the trend.
#'
#' 4. When a trend is detected, the Theil-Sen estimator is used to compute the
#'    slope of the trend and its confidence interval. The slope provides the
#'    magnitude and direction of the detected trend.
#'
#' 5. Since non-parametric trend tests such as Mann-Kendall can be affected by
#'    serial correlation, the residuals obtained after removing the estimated
#'    trend are checked for autocorrelation. ACF and PACF plots are saved and
#'    the Ljung-Box test is applied to the residuals.
#'
#'    This diagnostic step is included because computer system measurements may
#'    be autocorrelated, especially when samples are repeatedly collected from
#'    the same system at short time intervals, such as seconds or minutes.
#'
#'    When autocorrelation is detected, modified Mann-Kendall tests are computed
#'    as robustness checks. This helps determine whether a trend detected by the
#'    initial heuristic remains statistically robust after accounting for
#'    autocorrelation and it supports a more cautious interpretation of weak
#'    trends or near-zero slopes that may be encountered.
#'
#' 6. The results are saved in an output dataframe and the corresponding trend
#'    plot is generated. The procedure is then repeated for the next variable/
#'    CSV column.


trend_analysis <- function(exclude_list = c(1), csv_path, task_name,
                           env_name, row_cutoff,
                           aggregate = FALSE, dt_position = 1,
                           aggr_FUN = "median", aggr_align = "minutes",
                           aggr_period = 1, plot_dir,
                           tz = "America/Sao_Paulo") {

  print(paste("We are currently analyzing the following dataset:",
              basename(csv_path)))

  # 1. Read CSV, row cuttoff for coherence and parsing issues
  datain <- readr::read_csv(csv_path, show_col_types = FALSE)
  if (is.na(row_cutoff)) {
    stop("NO VALID CUTOFF FOR CSV!")
  } else {
    datain <- datain[1:row_cutoff, ]
  }

  parse_issues <- problems(datain)
  if (nrow(parse_issues) > 0) {
    print(paste("Parsing problems found in:", basename(csv_path)))
    print(parse_issues)
    if (aggregate) {
      datain <- datain[complete.cases(datain), ]
    }
  } else {
    print(paste("No parsing problems found in:", basename(csv_path)))
  }

  # Temporary dataframe to save the results of the trend analysis
  dataout <- data.frame(
    task = character(),
    env = character(),
    parameter = character(),

    cox_stuart_statistic = numeric(),
    cox_stuart_pvalue = numeric(),
    rho_spearman = numeric(),
    spearman_pvalue = numeric(),
    tau_kendall = numeric(),
    mann_kendall_pvalue = numeric(),

    ljung_box_pvalue = numeric(),

    mann_kendall_HR_corrected = numeric(),
    mann_kendall_YW_corrected = numeric(),
    mann_kendall_PW_von_storch = numeric(),
    mann_kendall_TFPW_yue = numeric(),

    trend_detected = logical(),

    sen_slope = numeric(),
    slope_upperCI_95 = numeric(),
    slope_lowerCI_95 = numeric(),
    sen_intercept = numeric(),
    intercept_upperCI_95 = numeric(),
    intercept_lowerCI_95 = numeric(),

    mean = numeric()
  )

  width  <- ncol(datain)

  # 2. Time Series loop
  for (col in setdiff(1:width, exclude_list)) {

    print(paste("We are currently analyzing the following variable:",
                colnames(datain)[col]))

    if (aggregate) {
      vut_ts <- aggregate_fun(datain[[col]], datain[[dt_position]],
                              aggr_FUN, aggr_align, aggr_period, tz)
    } else {
      # Create time series object with xts package without aggregation
      vut_ts <- xts(datain[[col]], as.POSIXct(datain[[dt_position]], tz = tz))
    }

    # current data ("variable under test" vut)
    vut    <- as.numeric(coredata(vut_ts))
    # time index
    date   <- index(vut_ts)

    # Remove samples with NA values.
    # keep is a logical vector: FALSE indicates that either the value in "vut"
    # or the corresponding timestamp in "date" is NA.
    # The indices where keep is FALSE identify the rows/samples to be discarded.
    keep <- !is.na(vut) & !is.na(date)
    vut  <- vut[keep]
    date <- date[keep]

    # Skip vut if constant
    if (length(unique(vut)) < 2) {
      print(paste("Skipping", colnames(datain)[col], ": constant series"))
      next
    }

    # 3. Statistical tests
    # Cox Stuart Trend test on vut
    # Selecting "two sided" the null hypothesis is tested against either
    # an upward trend or an downward trend
    cs_res <- cox.stuart.test(vut, "two.sided")
    #Save the result in a log file by printing it on the R console
    print(cs_res)
    print(paste("Cox-Stuart test p-value for", colnames(datain)[col],
                "is:", cs_res$p.value))

    # Spearman rho rank correlation
    # as.numeric(date) transform POSIXct datetime format to timestamp.
    # Spearman works on ranks so the transformation does not invalidate
    # the rho computation.
    sr_res <- cor.test(vut, as.numeric(date), alternative = "two.sided",
                       method = "spearman", exact = FALSE)
    print(sr_res)
    print(paste("Spearman test p-value for", colnames(datain)[col],
                "is:", sr_res$p.value))

    # "Classic" Mann Kendall trend test
    mk_res <- mk.test(vut)
    print(mk_res)
    print(paste("Mann-Kendall test p-value for", colnames(datain)[col],
                "is:", mk_res$p.value))

    # If at least 2 out of the 3 tests hint to a monotonic trend with a
    # pvalue < 0.05, we compute the slope with Theil Sen estimator.
    # trend_sig contains the logical value TRUE if a trend is detected,
    # FALSE if there is no trend.
    trend_sig <- !is.na(mk_res$p.value) &&
      !is.na(cs_res$p.value) &&
      !is.na(sr_res$p.value) && (
      (mk_res$p.value < 0.05 && cs_res$p.value < 0.05) ||
        (cs_res$p.value < 0.05 && sr_res$p.value < 0.05) ||
        (sr_res$p.value < 0.05 && mk_res$p.value < 0.05)
    )

    # Time index for slope estimation.
    # The slope should be computed against elapsed time and not
    # directly against raw timestamps for the "zyp" package.
    # Here x_time represents the elapsed time in minutes from
    # the first observation.
    # This is correct as long as the time series is sampled or aggregated at
    # regular one-minute intervals.
    x_time <- as.numeric(difftime(date, min(date), units = "mins"))

    if (trend_sig) {

      print("Trend DETECTED! Computing slope ...")

      # 4. Theil-sen estimator 95% confidence interval
      sslope   <- zyp.sen(vut ~ x_time)
      ci_slope <- confint.zyp(sslope, level = 0.95)
      print(sslope)
      print(ci_slope)

      slope     <- unname(sslope$coefficients[2])
      intercept <- unname(sslope$coefficients[1])

      # Residuals (y - y'), where y' is the expected value computed from
      # Theil-Sen.
      # This operation equals to "detrending" the time series
      residuals <- vut - (intercept + slope * x_time)

      #CHANGE 60 AS INPUT VALUE IF NEEDED -> TODO: parameter lag_max
      res_autocorr <- ac_pac_diagnostic(residuals, colnames(datain)[col],
                                        plot_dir, lag_max = 60)

      ljung_box_pvalue <- res_autocorr$ljung_box_pvalue
      autocorr_detected <- res_autocorr$autocorr_detected

      mk_mod_hr_pvalue <- NA_real_
      mk_mod_y_pvalue <- NA_real_
      mk_mod_pw_pvalue <- NA_real_
      mk_mod_pwy_pvalue <- NA_real_

      if (isTRUE(autocorr_detected)) {

        # Autocorrelation corrected mk tests for a more accurate pvalue
        # Hamed-Rao
        mk_mod_hr_res <- mmkh(vut)
        print(mk_mod_hr_res)
        mk_mod_hr_pvalue <- mk_mod_hr_res[["new P-value"]]
        print(paste("Hamed-Rao Mann-Kendall test p-value for",
                    colnames(datain)[col], "is:", mk_mod_hr_pvalue))
        # Yue Wang
        mk_mod_y_res <- mmky(vut)
        print(mk_mod_y_res)
        mk_mod_y_pvalue <- mk_mod_y_res[["new P-value"]]
        print(paste("Yue-Wang Mann-Kendall test p-value for",
                    colnames(datain)[col], "is:", mk_mod_y_pvalue))
        # Prewhitened
        mk_mod_pw_res <- pwmk(vut)
        print(mk_mod_pw_res)
        mk_mod_pw_pvalue <- mk_mod_pw_res[["P-value"]]
        print(paste("Prewhitened von Storch Mann-Kendall test p-value for",
                    colnames(datain)[col], "is:", mk_mod_pw_pvalue))
        # Prewhitened Yue
        mk_mod_pwy_res <- tfpwmk(vut)
        print(mk_mod_pwy_res)
        mk_mod_pwy_pvalue <- mk_mod_pwy_res[["P-value"]]
        print(paste("Prewhitened Yue et al. test p-value for",
                    colnames(datain)[col], "is:", mk_mod_pwy_pvalue))

      }

      # Confidence-interval slopes
      slope_lci     <- unname(ci_slope[2])
      slope_uci     <- unname(ci_slope[4])
      intercept_lci <- unname(ci_slope[1])
      intercept_uci <- unname(ci_slope[3])

      plot_fun(trend_sig, sslope, ci_slope, vut, date, x_time,
               datain, col, plot_dir)

      # OUTPUT dataframe
      dataout[nrow(dataout) + 1, ] <- data.frame(
        task = task_name,
        env = env_name,
        parameter = colnames(datain)[col],

        cox_stuart_statistic = unname(cs_res$statistic),
        cox_stuart_pvalue = cs_res$p.value,
        rho_spearman = unname(sr_res$estimate),
        spearman_pvalue = sr_res$p.value,
        tau_kendall = unname(mk_res$estimates[3]),
        mann_kendall_pvalue = mk_res$p.value,

        ljung_box_pvalue = ljung_box_pvalue,

        mann_kendall_HR_corrected = mk_mod_hr_pvalue,
        mann_kendall_YW_corrected = mk_mod_y_pvalue,
        mann_kendall_PW_von_storch = mk_mod_pw_pvalue,
        mann_kendall_TFPW_yue = mk_mod_pwy_pvalue,

        trend_detected = trend_sig,

        sen_slope = slope,
        slope_upperCI_95 = slope_uci,
        slope_lowerCI_95 = slope_lci,
        sen_intercept = intercept,
        intercept_upperCI_95 = intercept_uci,
        intercept_lowerCI_95 = intercept_lci,

        mean = mean(vut)
      )

    } else {

      print("NO Trend DETECTED!")

      # OUTPUT dataframe NO TREND
      dataout[nrow(dataout) + 1, ] <- data.frame(
        task = task_name,
        env = env_name,
        parameter = colnames(datain)[col],

        cox_stuart_statistic = unname(cs_res$statistic),
        cox_stuart_pvalue = cs_res$p.value,
        rho_spearman = unname(sr_res$estimate),
        spearman_pvalue = sr_res$p.value,
        tau_kendall = unname(mk_res$estimates[3]),
        mann_kendall_pvalue = mk_res$p.value,

        ljung_box_pvalue = NA_real_,

        mann_kendall_HR_corrected = NA_real_,
        mann_kendall_YW_corrected = NA_real_,
        mann_kendall_PW_von_storch = NA_real_,
        mann_kendall_TFPW_yue = NA_real_,

        trend_detected = trend_sig,

        sen_slope = NA_real_,
        slope_upperCI_95 = NA_real_,
        slope_lowerCI_95 = NA_real_,
        sen_intercept = NA_real_,
        intercept_upperCI_95 = NA_real_,
        intercept_lowerCI_95 = NA_real_,

        mean = mean(vut)
      )

      plot_fun(trend_sig = trend_sig, vut = vut, date = date,
               x_time = x_time, datain = datain, col = col,
               output_dir = plot_dir)
    }
  }

  # TESTING NOT DEFINITIVE
  # Multiple testing check using Benjamini-Hochberg correction.

  dataout$cox_stuart_pvalue_BH <- p.adjust(dataout$cox_stuart_pvalue, method = "BH")
  dataout$spearman_pvalue_BH <- p.adjust(dataout$spearman_pvalue, method = "BH")
  dataout$mann_kendall_pvalue_BH <- p.adjust(dataout$mann_kendall_pvalue, method = "BH")
  dataout$mann_kendall_HR_corrected_BH <- p.adjust(dataout$mann_kendall_HR_corrected, method = "BH")
  dataout$mann_kendall_YW_corrected_BH <- p.adjust(dataout$mann_kendall_YW_corrected, method = "BH")
  dataout$mann_kendall_PW_von_storch_BH <- p.adjust(dataout$mann_kendall_PW_von_storch, method = "BH")
  dataout$mann_kendall_TFPW_yue_BH <- p.adjust(dataout$mann_kendall_TFPW_yue,  method = "BH")

  return(dataout)
}

#' Plot function
#'
#' @param trend_sig Logical. TRUE if a statistically significant trend was
#'   detected for the current time series. If TRUE, the plot includes the
#'   Theil-Sen trend line and its confidence interval boundaries. If FALSE,
#'   only the original time series is plotted.
#'
#' @param sslope Object returned by zyp::zyp.sen(). It contains the estimated
#'   Theil-Sen intercept and slope. Required only when trend_sig is TRUE.
#'
#' @param ci_slope Confidence interval object returned by zyp::confint.zyp().
#'   Required only when trend_sig is TRUE.
#'
#' @param vut Numeric vector. Values of the variable under test.
#'
#' @param date POSIXct vector. Each value is a Datetime associated to
#'   a vut observation.
#'
#' @param x_time Numeric vector. Elapsed time from the first observation
#'   expressed in minutes. Used as x axis in the Theil-Sen computation
#'   in the trend function.
#'
#' @param datain Dataframe containing the dataset UT. Used to
#'   retrieve the name of the current variable for the plot title and output
#'   filename.
#'
#' @param col Integer. Column index of the variable currently being plotted.
#'
#' @param output_dir Character. Directory where the SVG plot is saved.
#'
#'
#' @details
#' This function generates a scatter plot of a time series. The output
#' filename is built from the analyzed variable name after sanitizing characters
#' that may not be valid in file paths.
#'
#' If trend_sig is TRUE, the function extracts the Theil-Sen slope and intercept
#' from "sslope" and draws the estimated trend line to the plot.
#'
#' If trend_sig is FALSE, the function only plots the observed values over time.

plot_fun <- function(trend_sig, sslope, ci_slope, vut, date, x_time,
                     datain, col, output_dir) {

  # Data we need for plotting
  data <- data.frame(date = date, x = x_time, vut  = vut)

  # Sanitize title
  fname <- gsub("[^A-Za-z0-9_-]", "_", colnames(datain)[col])

  trend_dir <- file.path(output_dir, "trend")
  dir.create(trend_dir, recursive = TRUE, showWarnings = FALSE)

  file_name <- paste0("plot_", fname, ".svg")
  file_path <- file.path(trend_dir, file_name)

  svg(file_path, width = 15, height = 5)
  on.exit(dev.off(), add = TRUE)

  if (trend_sig) {

    if (missing(sslope) || missing(ci_slope)) {
      stop("No slope for Regression line plotting!")
    }

    slope     <- unname(sslope$coefficients[2])
    intercept <- unname(sslope$coefficients[1])
    slope_lci <- unname(ci_slope[2])
    slope_uci <- unname(ci_slope[4])

    # We compute the intercept for the slope upper boundary and lower boundary
    intercept_lci_plot <- median(data$vut - slope_lci * data$x, na.rm = TRUE)
    intercept_uci_plot <- median(data$vut - slope_uci * data$x, na.rm = TRUE)

    # Trend line fitted inside the Time series scatter plot
    data$sen_fit     <- intercept          + slope     * data$x
    data$sen_fit_lci <- intercept_lci_plot + slope_lci * data$x
    data$sen_fit_uci <- intercept_uci_plot + slope_uci * data$x

    # Scatter plot + Regression line using geom_line (ggplot2 library)
    ggp <- ggplot(data, aes(x = x / 60, y = vut)) +
      geom_point(size = 0.7, alpha = 0.75, show.legend = FALSE) +
      geom_line(aes(y = sen_fit, color = "Estimated Trend", linetype = "Estimated Trend"), linewidth = 0.9) + 
      geom_line(aes(y = sen_fit_lci, color = "95% CI Lower", linetype = "95% CI Lower"), linewidth = 0.2) +
      geom_line(aes(y = sen_fit_uci, color = "95% CI Upper", linetype = "95% CI Upper"), linewidth = 0.2) +
      scale_color_manual(name = "", values = c(
                                               "Estimated Trend" = "red",
                                               "95% CI Lower" = "blue",
                                               "95% CI Upper" = "green")) +
      scale_linetype_manual(name = "", values = c(
                                                  "Estimated Trend" = "solid",
                                                  "95% CI Lower" = "dashed",
                                                  "95% CI Upper" = "dashed")) +
      scale_x_continuous(breaks = seq(0, max(data$x / 60), by = 2)) +
      labs(title = paste(colnames(datain)[col], "Time series"),
           x = "Time (hours)", y = colnames(datain)[col]) +
      theme_linedraw() + theme(axis.text.x = element_text(angle = 45, hjust = 1))

  } else {

    ggp <- ggplot(data, aes(x = date, y = vut)) +
      geom_point(size = 0.7, alpha = 0.75, show.legend = FALSE) +
      scale_x_datetime(date_breaks = "2 hour",date_labels = "%H:%M") +
      labs(title = paste(colnames(datain)[col], "Time series"),
           x = "Time (hours)", y = colnames(datain)[col]) + theme_linedraw() +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
  }
  print(ggp)
}

#' Aggregate function
#'
#' @param vut Numeric vector. Values of the variable under test.
#'
#' @param timestamp Unix timestamps associated with the observations of
#'   client-side parameters. In the current script, these timestamps
#'   are expected to be expressed in milliseconds.
#'
#' @param FUN Character. Aggregation function passed to
#'   highfrequency::aggregateTS(), e.g. "median" or "mean".
#'   Read aggregateTS() documentation for more info.
#'
#' @param align Character. Time unit used for aggregation and passed to the
#'   alignBy parameter of aggregateTS(), e.g. "minutes".
#'   Read aggregateTS() documentation for more info.
#'
#' @param period Integer. Size of the aggregation bucket passed to the
#'   alignPeriod parameter of aggregateTS(). For example,
#'   with align = "minutes" and period = 1, observations are aggregated
#'   into one-minute intervals.
#'   Read aggregateTS() documentation for more info.
#'
#' @param tz Character. Time zone used when converting Unix timestamps to
#'   POSIXct datetime values.
#'   Read aggregateTS() documentation for more info.
#'
#' @return An xts object containing the aggregated time series.
#'
#' @details
#' This function converts Unix timestamps to POSIXct datetime values, builds an
#' xts time series from the input values and aggregates the resulting time
#' series using highfrequency::aggregateTS().
#'
#' The constant UNIX_ms_convert is set to 1000 because the input timestamps are
#' expected to be in ms. If timestamps are provided in seconds instead,
#' this conversion factor should be changed to 1.
#'
#' This function is mainly used for client-side CSV files, where observations
#' may be collected at high frequency and must be aligned with server-side
#' metrics sampled approximately once per minute.

aggregate_fun <- function(vut, timestamp, FUN, align, period, tz) {

  # CHANGE IF THE TIMESTAMP IS NOT UNIX MS FORMAT FOR AGGREGATE TIME SERIES
  # TODO: make it a parameter
  UNIX_ms_convert <- 1000

  ts_xts  <- xts(vut, as.POSIXct(as.numeric(timestamp) / UNIX_ms_convert,
                                 origin = "1970-01-01", tz = tz))
  ts_xts_agg  <- aggregateTS(ts_xts, FUN = FUN, alignBy = align,
                             alignPeriod = period, tz = tz)

  return(ts_xts_agg)
}


#' Row cutoff function
#'
#' @param cl_csv_path Character. Path to the client-side CSV file. The first
#'   column is expected to contain Unix timestamps in milliseconds.
#'
#' @param sv_csv_path Character. Path to the server-side CSV file. The first
#'   column is expected to contain datetime values that can be converted to
#'   POSIXct. Difference between Datetime i and Datetime i+1 is always
#'   1 minute!
#'
#' @param proc_csv_path Character. Path to the process CSV file. The first
#'   column is expected to contain datetime values that can be converted to
#'   POSIXct. Difference between Datetime i and Datetime i+1 is always
#'   1 minute!
#'
#' @param exp_duration Numeric. Maximum experiment duration expressed in hours.
#'   The default value is 51.
#'
#' @param tz Character. Time zone used when converting timestamps and datetime
#'   values.
#'
#' @return A dataframe containing cl_row_cutoff and sv_row_cutoff computed.
#'
#' @details
#' This function computes a common time window for client and server datasets.
#' The goal is to keep both datasets temporally aligned before running the trend
#' analysis for coherent results.
#'
#' First the function computes the duration of both datasets in hours, then,
#' it selects the shortest value among the client duration, the server duration
#' and the expected experiment duration.
#' This selected duration is used to compute the last row to keep
#' in both CSV files. For the client dataset, the cutoff is computed using
#' Unix milliseconds. For the server dataset, the cutoff is computed using
#' POSIXct datetime values.

cutoff_row_fun <- function(cl_csv_path, sv_csv_path, proc_csv_path,
                           exp_duration = 51, tz = "America/Sao_Paulo") {

  # CHANGE IF THE TIMESTAMP IS NOT UNIX MS FORMAT
  # TODO: make it a parameter
  UNIX_ms_convert <- 1000

  sv_ok           <- FALSE
  cl_ok           <- FALSE
  proc_ok         <- FALSE
  sv_dur_hours    <- NA
  cl_dur_hours    <- NA
  sv_row_cutoff   <- NA
  cl_row_cutoff   <- NA
  proc_dur_hours  <- NA
  proc_row_cutoff <- NA


  if (!is_blank_path(cl_csv_path)) {
    cl_ok <- TRUE
    cl_datain <- read_csv(cl_csv_path, show_col_types = FALSE)

    # Client timestamp computations
    cl_start_timestamp <- as.POSIXct(as.numeric(cl_datain[[1]][1]) /
                                       UNIX_ms_convert, origin = "1970-01-01",
                                     tz = tz)
    cl_end_timestamp <- as.POSIXct(as.numeric(cl_datain[[1]][nrow(cl_datain)]) /
                                     UNIX_ms_convert, origin = "1970-01-01",
                                   tz = tz)
    cl_dur_hours <- as.numeric(difftime(cl_end_timestamp, cl_start_timestamp,
                                        units = "hours"))
  }

  if (!is_blank_path(sv_csv_path)) {
    sv_ok <- TRUE
    sv_datain <- read_csv(sv_csv_path, show_col_types = FALSE)

    # Server timestamp computations
    sv_start_timestamp <- as.POSIXct(sv_datain[[1]][1],
                                     format = "%Y-%m-%d %H:%M:%OS", tz = tz)
    sv_end_timestamp <- as.POSIXct(sv_datain[[1]][nrow(sv_datain)],
                                   format = "%Y-%m-%d %H:%M:%OS", tz = tz)
    sv_dur_hours <- as.numeric(difftime(sv_end_timestamp, sv_start_timestamp,
                                        units = "hours"))
  }

  if (!is_blank_path(proc_csv_path)) {
    proc_ok <- TRUE
    proc_datain <- read_csv(proc_csv_path, show_col_types = FALSE)

    proc_start_timestamp <- as.POSIXct(proc_datain[[1]][1],
                                       format = "%d/%m/%Y+%H:%M:%S", tz = tz)
    proc_end_timestamp <- as.POSIXct(proc_datain[[1]][nrow(proc_datain)],
                                     format = "%d/%m/%Y+%H:%M:%S", tz = tz)
    proc_dur_hours <- as.numeric(difftime(proc_end_timestamp,
                                          proc_start_timestamp,
                                          units = "hours"))
  }

  if (!sv_ok && !cl_ok && !proc_ok) {
    stop("NO Valid CSV files!")
  }

  # Default experiment duration is 51 hours.
  # If either the client, server or processes CSV is shorter,
  # use the shortest duration as the cutoff to keep all datasets
  # temporally consistent.
  target_hours <- min(cl_dur_hours, sv_dur_hours, proc_dur_hours,
                      exp_duration, na.rm = TRUE)

  if (cl_ok) {
    # cl_cutoff_ms is the timestamp in UNIX ms associated to
    # the first timestamp + experiment duration (e.g. 51 ore)
    # 60*60*1000 is the ms to hour conversion!
    cl_start_ms <- as.numeric(cl_datain[[1]][1])
    cl_cutoff_ms <- cl_start_ms + target_hours * 60 * 60 * 1000

    # Here we select the row inside the dataset that has timestamp equal
    # to "cl_cutoff_ms", then we store the index inside "cl_row_cutoff" var.
    cl_row_cutoff <- max(which(as.numeric(cl_datain[[1]]) <= cl_cutoff_ms))
  }

  if (sv_ok) {
    # Same operation for server.
    # Here timestamps are in seconds -> 60*60 conversion sec to hour!
    sv_cutoff_timestamp <- sv_start_timestamp + target_hours * 60 * 60
    # Datetime to timestamp conversion for the "timestamp cutoff"
    # that follows
    sv_timestamps <- as.POSIXct(sv_datain[[1]],
                                format = "%Y-%m-%d %H:%M:%OS", tz = tz)
    # Timestamp cutoff: we select the row that has timestamp equal to
    # sv_cutoff_timestamp like done before in client.
    sv_row_cutoff <- max(which(sv_timestamps <= sv_cutoff_timestamp))
  }

  if (proc_ok) {
    # Same operation done for server: proc.csv has samples every minute!.
    proc_cutoff_timestamp <- proc_start_timestamp + target_hours * 60 * 60
    proc_timestamps <- as.POSIXct(proc_datain[[1]],
                                  format = "%d/%m/%Y+%H:%M:%S", tz = tz)
    proc_row_cutoff <- max(which(proc_timestamps <= proc_cutoff_timestamp))
  }

  result <- data.frame(
    target_hours = target_hours,
    cl_duration_hours = cl_dur_hours,
    sv_duration_hours = sv_dur_hours,
    proc_duration_hours = proc_dur_hours,
    cl_row_cutoff = cl_row_cutoff,
    sv_row_cutoff = sv_row_cutoff,
    proc_row_cutoff = proc_row_cutoff
  )

  # Debug can be removed
  print("DEBUG client-server-proc duration times:")
  print(cl_dur_hours)
  print(sv_dur_hours)
  print(proc_dur_hours)

  return (result)
}

#' Autocorrelation function
#'
#' @param residuals Numeric vector. Residuals obtained after removing the
#'   estimated trend from the original time series.
#'
#' @param time_series_name Character. Name of the analyzed time series. It is
#'   used to build the ACF/PACF plot titles and output filenames.
#'
#' @param plot_dir Character. Directory where the ACF and PACF plots are saved.
#'
#' @param lag_max Integer. Maximum lag shown in the ACF and PACF plots. Since
#'   the analyzed series are usually sampled or aggregated at one-minute
#'   intervals, lag_max = 60 corresponds to one hour.
#'
#' @return A dataframe containing the Ljung-Box p-value and a logical flag
#'   indicating whether autocorrelation was detected.
#'
#' @details
#' This function performs autocorrelation diagnostics after trend.
#' It saves two SVG plots: the autocorrelation function (ACF) and the
#' partial autocorrelation function (PACF) of the detrended residuals
#' of the give time series.
#'
#' The Ljung-Box test is then applied to the residuals to check
#' for autocorrelation up to 30 lags, corresponding to 30 minutes for
#' one-minute sampled data.
#'
#' A Ljung-Box p-value below 0.05 is interpreted as evidence of
#' autocorrelation. In the main trend analysis, this result is used to decide
#' whether modified Mann-Kendall tests should be computed as robustness checks.

ac_pac_diagnostic <- function(residuals, time_series_name, plot_dir, lag_max) {

  fname <- gsub("[^A-Za-z0-9_-]", "_", time_series_name)

  acf_dir <- file.path(plot_dir, "acf")
  pacf_dir <- file.path(plot_dir, "pacf")

  dir.create(acf_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(pacf_dir, recursive = TRUE, showWarnings = FALSE)

  acf_file <- file.path(acf_dir, paste0("acf_", fname, ".svg"))
  pacf_file <- file.path(pacf_dir, paste0("pacf_", fname, ".svg"))

  residuals <- residuals[is.finite(residuals)]

  # Not enough residuals for the test
  if (length(residuals) < 3) {
    return(data.frame(
      ljung_box_pvalue = NA_real_,
      autocorr_detected = NA
    ))
  }

  lag_max <- min(lag_max, length(residuals) - 1)
  lb_lag  <- min(floor(lag_max / 2), length(residuals) - 1)


  # Lag 1 is the correlation between values that are ONE time period apart
  # lag.max=60 means that we compute the correlation between values
  # from k=1 to k=60 where 60 is chosen because the obs are 1 minute
  # apart so -> acf between 1-hour lagged time series max

  svg(acf_file, width = 8, height = 5)
  Acf(residuals, lag.max = lag_max, type = "correlation",
      main = paste("ACF residuals -", time_series_name))
  dev.off()

  svg(pacf_file, width = 8, height = 5)
  Acf(residuals, lag.max = lag_max, type = "partial",
      main = paste("PACF residuals -", time_series_name))
  dev.off()

  lb_res <- Box.test(residuals, lag = lb_lag, type = "Ljung-Box")

  #Default
  autocorr_detected <- FALSE

  if (!is.na(lb_res$p.value) && lb_res$p.value < 0.05) {
    autocorr_detected <- TRUE
  }

  return (data.frame(
                     ljung_box_pvalue =  lb_res$p.value,
                     autocorr_detected = autocorr_detected)
  )
}

#' Auxiliary function
#' @param x Character. A CSV path to check if It is empty.
#'
#' @details
#' The following aux function check the input and
#' returns TRUE if the path was empty.

is_blank_path <- function(x) {
  length(x) == 0 || is.na(x) || trimws(x) %in% c("", "NA", "NO_CSV")
}

#' Main entry point for the trend analysis script
#'
#' @param ... Unused. Command-line arguments are read internally using
#'   commandArgs(trailingOnly = TRUE).
#'
#' @return This function does not return an object. It creates output
#'   directories, writes log files, runs the trend analysis on the provided
#'   server and/or client CSV files and saves the resulting CSV files.
#'
#' @details
#' This function is the main entry point of the script. It reads command-line
#' arguments, initializes the output directories, redirects console output and
#' messages to a log file, aligns client and server datasets to a common time
#' window and runs the trend analysis.
#'
#' The script expects the following command-line format:
#'
#' "Rscript script.R <server_csv> <client_csv> <process_csv> <task_name> <env_name>"
#'
#' The expected arguments are:
#'
#' 1. server_csv: absolute or relative path to the CSV file containing
#'    server-side monitoring data.
#' 2. client_csv: absolute or relative path to the CSV file containing
#'    client-side/JMeter data.
#' 3. process_csv: absolute or relative path to the CSV file containing
#'    process data like RSS and VSZ.
#' 4. task_name: name of the benchmark task, e.g. "monitor",
#'    "ImageConverter", "uptime", or "credit_card".
#' 5. env_name: language and framework identifier, e.g. "python_django",
#'    "python_fastapi", or "python_aiohttp".
#'
#' The function creates three output directories:
#'
#' - Rscript/csv_data_analysis_results: stores the trend-analysis CSV outputs.
#' - Rscript/logs: stores the execution log.
#' - Rscript/plots: stores trend, ACF, and PACF plots.
#'
#' Client, process and server files are first aligned using cutoff_row_fun(),
#' so all datasets are analyzed over the same time window. The server and
#' process CSVs are analyzed without aggregation, while the
#' client CSV is aggregated into one-minute intervals before analysis.
#'
#' If a "NO_CSV" string is provided for any csv path arg, the
#' corresponding analysis step is skipped.

main <- function(...) {
  config <- list(
    output_dir = file.path(getwd(), "Rscript/csv_data_analysis_results"),
    logs_dir   = file.path(getwd(), "Rscript/logs"),
    plot_dir   = file.path(getwd(), "Rscript/plots"),
    exclude_server_cols = c(1),
    exclude_client_cols = c(1),
    exclude_process_cols = c(1, 2, 3, 7, 8, 9),
    tz = "America/Sao_Paulo"
  )

  args <- commandArgs(trailingOnly = TRUE)

  if (length(args) != 5) {
    stop("Check args! The cmd should be in the following format:
      Rscript script.R <server_csv> <client_csv> <process_csv> <task_name> <env_name>
      You can give write NO_CSV as an argument for any missing csvs! ")
  }

  server_csv <- args[1]
  client_csv <- args[2]
  proc_csv   <- args[3]
  task_name  <- args[4]
  env_name   <- args[5]

  path_plots <- file.path(config$plot_dir,
                          paste(env_name, task_name, sep = "_"))

  dir.create(config$output_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(path_plots, recursive = TRUE, showWarnings = FALSE)
  dir.create(config$logs_dir, recursive = TRUE, showWarnings = FALSE)

  log_path <- file.path(
    config$logs_dir,
    paste0("log_", env_name, "_", task_name, ".txt")
  )

  log_con <- file(log_path, open = "wt")
  sink(log_con, type = "output")
  sink(log_con, type = "message")

  # When the main closes correctly or with an error state
  # we close all files (even the log)
  on.exit(closeAllConnections(), add = TRUE)

  row_cutoff_res <- cutoff_row_fun(client_csv, server_csv, proc_csv,
                                   tz = config$tz)

  # You should give an "NO_CSV" string in input if you do not want
  # to analyze a server or client csv
  if (!is_blank_path(server_csv)) {

    dataout_server <- trend_analysis(
      exclude_list = config$exclude_server_cols,
      csv_path = server_csv,
      task_name = task_name,
      env_name = env_name,
      row_cutoff = row_cutoff_res$sv_row_cutoff,
      tz = config$tz,
      plot_dir = path_plots
    )

    write_csv(
      dataout_server,
      file.path(config$output_dir, paste(env_name, task_name,
                                         "data_test_server.csv", sep = "_"))
    )
  }

  if (!is_blank_path(client_csv)) {

    dataout_client <- trend_analysis(
      exclude_list = config$exclude_client_cols,
      csv_path = client_csv,
      task_name = task_name,
      env_name = env_name,
      row_cutoff = row_cutoff_res$cl_row_cutoff,
      aggregate = TRUE,
      tz = config$tz,
      plot_dir = path_plots
    )

    write_csv(
      dataout_client,
      file.path(config$output_dir, paste(env_name, task_name,
                                         "data_test_client.csv", sep = "_"))
    )
  }

  if (!is_blank_path(proc_csv)) {

    dataout_process <- trend_analysis(
      exclude_list = config$exclude_process_cols,
      csv_path = proc_csv,
      task_name = task_name,
      env_name = env_name,
      row_cutoff = row_cutoff_res$proc_row_cutoff,
      dt_position = 8,
      tz = config$tz,
      plot_dir = path_plots
    )

    write_csv(
      dataout_process,
      file.path(config$output_dir, paste(env_name, task_name,
                                         "data_test_process.csv", sep = "_"))
    )
  }
}

# START SCRIPT
main()